import { Route, Routes } from 'react-router'
import DevEvents from './pages/DevEvents.tsx'
import Director from './pages/Director.tsx'
import LinkPage from './pages/LinkPage.tsx'

export default function App() {
  return (
    <Routes>
      <Route path="/dev/events/:callId" element={<DevEvents />} />
      <Route path="/director" element={<Director />} />
      <Route path="/s/:token" element={<LinkPage />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}

function NotFound() {
  return (
    <main className="p-8 font-mono text-sm text-stone-500">
      Nothing here. Try <code>/dev/events/&lt;callId&gt;</code>.
    </main>
  )
}
