import { Route, Routes } from 'react-router'
import DevEvents from './pages/DevEvents.tsx'
import DevScreenshots from './pages/DevScreenshots.tsx'
import Director from './pages/Director.tsx'
import LinkPage from './pages/LinkPage.tsx'

export default function App() {
  return (
    <Routes>
      <Route path="/dev/events/:callId" element={<DevEvents />} />
      {import.meta.env.DEV && <Route path="/dev/screenshots" element={<DevScreenshots />} />}
      <Route path="/director" element={<Director />} />
      <Route path="/s/:token" element={<LinkPage />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}

function NotFound() {
  return (
    <main className="space-y-2 p-8 font-mono text-sm text-stone-500">
      <p className="text-stone-800">Call Link Demo (dev)</p>
      <p>
        Customer link: <code>/s/&lt;token&gt;</code>. Start a call with <code>make live S=order</code> to get one.
      </p>
      {import.meta.env.DEV && (
        <p>
          Screenshots: <a className="underline" href="/dev/screenshots">/dev/screenshots</a>
        </p>
      )}
      <p>
        Raw events: <code>/dev/events/&lt;callId&gt;</code>
      </p>
    </main>
  )
}
