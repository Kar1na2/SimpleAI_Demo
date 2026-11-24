"""The LLM agent: Anthropic Messages API with tool use, one message history per call.

- Customer turns arrive as user messages holding text_heard only. System notes (call
  connected, silence nudge, Confirm tapped) arrive as bracketed user text.
- Each `text` block the model returns is one spoken agent turn, emitted in order and
  interleaved with tool calls. The loop runs tool_use until the model ends its turn.

Model settings, chosen for Claude Sonnet 5.5 (ANTHROPIC_MODEL):
- No `temperature`: on Sonnet 5.5 a non-default value is a 400. Low effort is the closest
  lever ("low temperature" in the brief).
- Adaptive thinking at low effort. We tried thinking off first ({"type": "between_tools"}), but
  with nowhere private to plan, the model planned out loud in short `text` blocks before tool
  calls ("Jamie says the page is wrong, so I fix it right away..."), and TTS would speak those.
  With adaptive thinking it plans in `thinking` blocks, which are never spoken and are passed
  back unchanged (the history is append-only). At low effort it skips thinking on most turns.
- MAX_TOKENS 1024 rather than ~400, because thinking counts toward it. The phone-register
  prompt keeps the spoken reply to one or two sentences.
- Prompt caching (top-level cache_control): system + tools + history prefix are reused each step.
- Server-side refusal fallback ("default"), the Anthropic-recommended default for Sonnet 5.5.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

import anthropic

from app.call.scenario import Scenario
from app.call.tools import ToolContext, run_tool, tool_definitions
from app.config import get_settings

log = logging.getLogger(__name__)

EFFORT = "low"
MAX_TOKENS = 1024  # thinking counts toward this; see module docstring
MAX_STEPS_PER_TURN = 10  # model requests inside one agent turn (tool loop iterations)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
GOODBYE_NOTE = (
    "You haven't said anything out loud since the caller's last message. Say your one-sentence "
    "wrap-up and goodbye now, as your reply, with no tool calls. The call ends right after."
)

# USD per million tokens: input, output, 5-minute cache write, cache read.
PRICES = {"claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.10)}


class AgentError(RuntimeError):
    pass


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def add(self, u: Any) -> None:
        self.requests += 1
        self.input_tokens += u.input_tokens or 0
        self.output_tokens += u.output_tokens or 0
        self.cache_creation_input_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(u, "cache_read_input_tokens", 0) or 0

    def cost_usd(self, model: str) -> float | None:
        if model not in PRICES:
            return None
        p_in, p_out, p_write, p_read = PRICES[model]
        return (
            self.input_tokens * p_in
            + self.output_tokens * p_out
            + self.cache_creation_input_tokens * p_write
            + self.cache_read_input_tokens * p_read
        ) / 1e6

    def as_dict(self, model: str) -> dict[str, Any]:
        return {**asdict(self), "model": model, "cost_usd": self.cost_usd(model)}


class Agent:
    def __init__(self, scenario: Scenario, ctx: ToolContext, on_say: Callable[[str], Awaitable[None]]):
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise AgentError("ANTHROPIC_API_KEY is not set in backend/.env")
        # Key passed explicitly: backend/.env values never enter os.environ (see CLAUDE.md > Secrets).
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=60)
        self.model = settings.anthropic_model
        self.system = scenario.system_prompt
        self.tools = tool_definitions(scenario.name)
        self.ctx = ctx
        self.on_say = on_say
        self.messages: list[dict[str, Any]] = []
        self.usage = Usage()
        self.unspoken: list[str] = []  # between-tool notes that came back as thinking blocks

    async def respond(self, user_texts: list[str]) -> None:
        """One agent turn: deliver the user content, then speak and call tools until the model stops."""
        self.messages.append({"role": "user", "content": [{"type": "text", "text": t} for t in user_texts]})
        spoke = False  # did anything get said out loud since this input?
        goodbye_requested = False  # at most once per turn: see the end_call branch below
        for _ in range(MAX_STEPS_PER_TURN):
            ask_goodbye = False
            response = await self._create()
            self.messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                category = getattr(response.stop_details, "category", None)
                raise AgentError(f"model refused (category: {category})")
            if response.stop_reason == "max_tokens":
                log.warning("call %s: agent hit max_tokens=%d", self.ctx.call_id, MAX_TOKENS)

            results = []
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    await self.on_say(block.text.strip())
                    spoke = True
                elif block.type == "thinking" and (block.thinking or "").strip():
                    self.unspoken.append(block.thinking.strip())
                    log.info("call %s: unspoken note: %s", self.ctx.call_id, block.thinking.strip())
                elif block.type == "tool_use":
                    content, is_error = await run_tool(self.ctx, block.name, block.input)
                    if block.name == "end_call" and not is_error and not spoke and not goodbye_requested:
                        # Text written just before a tool call can come back as an (empty) thinking
                        # block on Sonnet 5.5, so the wrap-up never gets spoken. Give the goodbye
                        # one more request, where it lands as the final text block.
                        content = json.dumps({"status": "ending", "note": GOODBYE_NOTE})
                        goodbye_requested = ask_goodbye = True
                    result: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id, "content": content}
                    if is_error:
                        result["is_error"] = True
                    results.append(result)

            if not results:
                return  # end_turn (or max_tokens with no pending tool call)
            # Every tool_use needs its tool_result, even when the call is ending.
            self.messages.append({"role": "user", "content": results})
            if self.ctx.end_requested:
                if ask_goodbye:
                    log.info("call %s: end_call with nothing said; asking for the goodbye", self.ctx.call_id)
                    continue
                return  # hung up: no more model requests on this call
        raise AgentError(f"agent didn't finish its turn within {MAX_STEPS_PER_TURN} model requests")

    async def _create(self):
        try:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=self.system,
                tools=self.tools,
                messages=self.messages,
                thinking={"type": "adaptive"},
                output_config={"effort": EFFORT},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise AgentError(f"Anthropic rejected the API key: {e.message}") from e
        except anthropic.BadRequestError as e:
            raise AgentError(f"Anthropic API rejected the request: {e.message}") from e
        except anthropic.RateLimitError as e:
            raise AgentError(f"Anthropic rate limit (after retries): {e.message}") from e
        except anthropic.APIStatusError as e:
            raise AgentError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise AgentError(f"can't reach the Anthropic API: {e}") from e
        self.usage.add(response.usage)
        return response
