import { Link, NavLink, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useGuild, useGuildId, useGuilds, useMeta } from '../api/hooks'
import { hasFeature, PAGES, type PageEntry } from '../pages'
import { MemberName } from './Names'
import { Card } from './ui'

export function Layout() {
  const guildId = useGuildId()
  const guilds = useGuilds().data ?? []
  const guild = useGuild()
  const meta = useMeta().data
  const navigate = useNavigate()
  const location = useLocation()
  const [params, setParams] = useSearchParams()
  const member = params.get('member')
  // Keep the member filter while moving between pages.
  const search = member ? `?member=${member}` : ''
  const section = location.pathname.split('/')[3] ?? ''

  return (
    <div className="flex min-h-screen">
      <nav className="sticky top-0 flex h-screen w-56 shrink-0 flex-col gap-1 overflow-y-auto border-r border-stone-200 bg-white p-3">
        <Link to="/" className="mb-2 flex items-center gap-2 px-2 text-lg font-bold text-stone-900 hover:no-underline">
          <img src="/favicon.svg" alt="" className="h-6 w-6" /> Beans Bot
        </Link>
        <select
          className="mb-3 rounded-md border border-stone-300 px-2 py-1 text-sm"
          value={guildId}
          onChange={(e) => navigate(`/g/${e.target.value}/${section}`)}
        >
          {guilds.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
              {g.active ? '' : ' (inactive)'}
            </option>
          ))}
        </select>
        {PAGES.map((page) => (
          <NavLink
            key={page.path}
            end={page.path === ''}
            to={`/g/${guildId}/${page.path}${search}`}
            className={({ isActive }) =>
              `rounded-md px-2 py-1 text-sm text-stone-800 hover:bg-stone-100 hover:no-underline ${isActive ? 'bg-amber-100 font-semibold' : ''} ${
                hasFeature(guild?.features, page) ? '' : 'opacity-40'
              }`
            }
          >
            {page.label}
          </NavLink>
        ))}
        <div className="mt-auto px-2 pt-3 text-xs text-stone-500">
          {meta ? (meta.connected ? `Connected as ${meta.bot?.name}` : 'Not connected to Discord') : ''}
        </div>
      </nav>
      <main className="min-w-0 flex-1 p-6">
        {member && (
          <div className="mb-4 flex items-center gap-2 rounded-md bg-amber-50 px-3 py-2 text-sm">
            Showing records for <MemberName id={member} link={false} />
            <button
              className="ml-auto text-amber-800 hover:underline"
              onClick={() => {
                params.delete('member')
                setParams(params)
              }}
            >
              Show everyone
            </button>
          </div>
        )}
        <Outlet />
      </main>
    </div>
  )
}

/** Shows the page only when the guild has one of its features. */
export function FeatureGate({ page, children }: { page: PageEntry; children: React.ReactNode }) {
  const guild = useGuild()
  if (guild && !hasFeature(guild.features, page)) {
    return (
      <Card>
        <p className="text-stone-600">
          <strong>{page.label}</strong> is not set up for this server. Add its section to the server's config to turn it on.
        </p>
      </Card>
    )
  }
  return <>{children}</>
}
