import { Link } from 'react-router-dom'
import { useGuildId, useGuilds, useMeta } from '../api/hooks'
import { Badge, Card, Loaded, PageHeader, Stat } from '../components/ui'
import { SpeakCard, SyncButton } from './actions/BotActions'

function duration(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes}m` : `${minutes}m`
}

export function Overview() {
  const meta = useMeta()
  const guilds = useGuilds()
  const guildId = useGuildId()
  return (
    <>
      <PageHeader title="Overview" />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="The bot" actions={<SyncButton />}>
          <Loaded query={meta}>
            {(m) => (
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  {m.bot?.avatar_url && <img src={m.bot.avatar_url} alt="" className="h-10 w-10 rounded-full" />}
                  <div>
                    <div className="font-semibold">{m.bot?.name ?? 'No bot'}</div>
                    {m.connected ? <Badge color="green">Connected</Badge> : <Badge color="red">Not connected to Discord</Badge>}
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <Stat label="Up for">{duration(m.uptime_seconds)}</Stat>
                  <Stat label="Latency">{m.latency_ms === null ? '-' : `${Math.round(m.latency_ms)} ms`}</Stat>
                  <Stat label="Time zone">{m.timezone}</Stat>
                </div>
                <div className="text-sm text-stone-600">
                  Currencies: {m.currencies.map((c) => `${c.name} (${c.base_units} base units)`).join(', ')}
                </div>
                <div className="text-sm text-stone-600">
                  Cogs: {m.cogs.length ? m.cogs.join(', ') : 'none'}. Classifier {m.classifier_loaded ? 'loaded' : 'not loaded'}.
                </div>
              </div>
            )}
          </Loaded>
        </Card>
        <Card title="Servers">
          <Loaded query={guilds}>
            {(list) => (
              <ul className="space-y-3">
                {list.map((g) => (
                  <li key={g.id} className={g.id === guildId ? 'rounded-md bg-amber-50 p-2' : 'p-2'}>
                    <div className="flex flex-wrap items-center gap-2">
                      <Link to={`/g/${g.id}`} className="font-semibold">
                        {g.name}
                      </Link>
                      {!g.active && <Badge>inactive</Badge>}
                      {g.connected ? <Badge color="green">{g.member_count} members</Badge> : <Badge color="red">not connected</Badge>}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {Object.entries(g.features).map(([name, on]) => (
                        <Badge key={name} color={on ? 'amber' : 'stone'}>
                          {on ? name : <s>{name}</s>}
                        </Badge>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Loaded>
        </Card>
        <SpeakCard />
      </div>
    </>
  )
}
