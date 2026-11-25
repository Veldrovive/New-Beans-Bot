import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { useDirectory, useGuildId, useMeta } from '../api/hooks'

/** A member's avatar and display name from the directory. Falls back to the raw ID. Clicking it filters by them. */
export function MemberName({ id, link = true }: { id: string | null | undefined; link?: boolean }) {
  const guildId = useGuildId()
  const directory = useDirectory(guildId).data
  const meta = useMeta().data
  const location = useLocation()
  const [params] = useSearchParams()
  if (!id) return <span className="text-stone-400">-</span>
  const isHouse = id === meta?.house_user_id || id === meta?.bot?.id
  const member = directory?.members[id]
  const label = isHouse ? 'the house' : (member?.display_name ?? id)
  const content = (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap" title={member ? `${member.name} (${id})` : id}>
      {member?.avatar_url ? <img src={member.avatar_url} alt="" className="h-5 w-5 rounded-full" /> : <span className="h-5 w-5 rounded-full bg-stone-300" />}
      <span className={isHouse ? 'italic' : member ? '' : 'font-mono text-xs'}>{label}</span>
    </span>
  )
  if (!link || isHouse) return content
  const next = new URLSearchParams(params)
  next.set('member', id)
  return (
    <Link to={`${location.pathname}?${next}`} className="text-inherit hover:underline">
      {content}
    </Link>
  )
}

export function ChannelName({ id }: { id: string | null | undefined }) {
  const directory = useDirectory(useGuildId()).data
  if (!id) return <span className="text-stone-400">-</span>
  const channel = directory?.channels[id]
  return channel ? <span title={id}>#{channel.name}</span> : <span className="font-mono text-xs">#{id}</span>
}

export function RoleName({ id }: { id: string | null | undefined }) {
  const directory = useDirectory(useGuildId()).data
  if (!id) return <span className="text-stone-400">-</span>
  const role = directory?.roles[id]
  return role ? (
    <span title={id} style={{ color: role.color ?? undefined }}>
      @{role.name}
    </span>
  ) : (
    <span className="font-mono text-xs">@{id}</span>
  )
}

export function discordUrl(guildId: string, channelId: string, messageId?: string | null): string {
  return `https://discord.com/channels/${guildId}/${channelId}${messageId ? `/${messageId}` : ''}`
}

export function DiscordLink({ channelId, messageId, children }: { channelId: string | null | undefined; messageId?: string | null; children?: React.ReactNode }) {
  const guildId = useGuildId()
  if (!channelId) return <span className="text-stone-400">-</span>
  return (
    <a href={discordUrl(guildId, channelId, messageId)} target="_blank" rel="noreferrer">
      {children ?? 'Open in Discord'}
    </a>
  )
}

/** The member the pages are filtered to (`?member=`), if any. */
export function useMemberFilter(): string | undefined {
  const [params] = useSearchParams()
  return params.get('member') ?? undefined
}
