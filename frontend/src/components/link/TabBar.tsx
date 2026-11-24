export type TabId = 'live' | 'transcript' | 'notes' | 'actions' | 'confirm'
export type TabDef = { id: TabId; label: string; badge?: number; attention?: boolean }

type Props = {
  tabs: TabDef[]
  active: TabId
  onSelect: (id: TabId) => void
  follow: boolean
  onFollowChange: (on: boolean) => void
  compact?: boolean
}

function FollowToggle({ follow, onFollowChange }: { follow: boolean; onFollowChange: (on: boolean) => void }) {
  return (
    <label className="flex shrink-0 cursor-pointer items-center gap-2 text-xs text-stone-500 select-none">
      Follow along
      <button
        type="button"
        role="switch"
        aria-checked={follow}
        aria-label="Follow along"
        data-testid="follow-toggle"
        onClick={() => onFollowChange(!follow)}
        className={`relative h-[18px] w-8 shrink-0 rounded-full transition-colors duration-200 ${follow ? 'bg-accent' : 'bg-stone-300'}`}
      >
        <span
          className={`absolute top-[2px] h-[14px] w-[14px] rounded-full bg-white shadow-sm transition-[left] duration-200 ${follow ? 'left-[16px]' : 'left-[2px]'}`}
        />
      </button>
    </label>
  )
}

export function TabBar({ tabs, active, onSelect, follow, onFollowChange, compact }: Props) {
  if (compact)
    return (
      <div className="border-b border-black/[0.06]">
        <div role="tablist" className="-mb-px flex gap-0.5 overflow-x-auto px-2 [scrollbar-width:none]">
          {tabs.map((t) => (
            <Tab key={t.id} t={t} selected={t.id === active} onSelect={onSelect} compact />
          ))}
        </div>
        <div className="flex justify-end border-t border-black/[0.04] bg-stone-50/60 px-4 py-1.5">
          <FollowToggle follow={follow} onFollowChange={onFollowChange} />
        </div>
      </div>
    )
  return (
    <div className="flex items-center justify-between border-b border-black/[0.06] px-5">
      <div role="tablist" className="-mb-px flex gap-1 overflow-x-auto">
        {tabs.map((t) => (
          <Tab key={t.id} t={t} selected={t.id === active} onSelect={onSelect} />
        ))}
      </div>
      <div className="ml-3">
        <FollowToggle follow={follow} onFollowChange={onFollowChange} />
      </div>
    </div>
  )
}

function Tab({ t, selected, onSelect, compact }: { t: TabDef; selected: boolean; onSelect: (id: TabId) => void; compact?: boolean }) {
  return (
    <button
      role="tab"
      aria-selected={selected}
      data-testid={`tab-${t.id}`}
      onClick={() => onSelect(t.id)}
      className={`relative flex shrink-0 items-center gap-1.5 border-b-2 py-3 text-sm transition-colors duration-150 ${compact ? 'px-2.5' : 'px-3'} ${
        selected ? 'border-accent font-medium text-stone-900' : 'border-transparent text-stone-500 hover:text-stone-800'
      } ${t.attention ? 'animate-fade-in' : ''}`}
    >
      {/* A new Confirm tab pulses a few times when it appears, selected or not. */}
      {t.attention && <span className="pointer-events-none absolute inset-x-1 inset-y-2 rounded-md animate-pulse-ring" aria-hidden />}
      <span className={t.attention && !selected ? 'font-medium text-accent' : ''}>{t.label}</span>
      {!!t.badge && !selected && (
        <span className="min-w-5 rounded-full bg-accent px-1.5 text-center text-[11px] leading-5 font-medium text-white tabular-nums">
          {t.badge}
        </span>
      )}
    </button>
  )
}
