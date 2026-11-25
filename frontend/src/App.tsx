import { Navigate, Route, Routes } from 'react-router-dom'
import { useGuilds } from './api/hooks'
import { FeatureGate, Layout } from './components/Layout'
import { Loaded } from './components/ui'
import { Overview } from './pages/Overview'
import { PAGES } from './pages'
import { PAGE_COMPONENTS } from './pages/registry'

function Home() {
  const guilds = useGuilds()
  return (
    <div className="p-6">
      <Loaded query={guilds}>
        {(list) => {
          const first = list.find((g) => g.active) ?? list[0]
          return first ? <Navigate to={`/g/${first.id}`} replace /> : <p>No servers are configured.</p>
        }}
      </Loaded>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/g/:guildId" element={<Layout />}>
        <Route index element={<Overview />} />
        {PAGES.filter((p) => p.path && PAGE_COMPONENTS[p.path]).map((page) => {
          const Page = PAGE_COMPONENTS[page.path]
          return (
            <Route
              key={page.path}
              path={`${page.path}/*`}
              element={
                <FeatureGate page={page}>
                  <Page />
                </FeatureGate>
              }
            />
          )
        })}
        <Route path="*" element={<p>No such page.</p>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
