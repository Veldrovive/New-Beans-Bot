import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api } from './client'
import type { Directory, GuildSummary, Meta } from './types'

/** Query keys for a guild's data start with this, so one invalidation refreshes the whole guild. */
export const guildKey = (guildId: string) => ['guild', guildId] as const

export function useGuildId(): string {
  const { guildId } = useParams()
  return guildId ?? ''
}

export function useMeta() {
  return useQuery({ queryKey: ['meta'], queryFn: () => api<Meta>('GET', '/api/meta') })
}

export function useGuilds() {
  return useQuery({ queryKey: ['guilds'], queryFn: () => api<GuildSummary[]>('GET', '/api/guilds') })
}

export function useGuild(): GuildSummary | undefined {
  const guildId = useGuildId()
  return useGuilds().data?.find((g) => g.id === guildId)
}

export function useDirectory(guildId: string) {
  return useQuery({
    queryKey: [...guildKey(guildId), 'directory'],
    queryFn: () => api<Directory>('GET', `/api/guilds/${guildId}/directory`),
    enabled: !!guildId,
    staleTime: 60_000,
  })
}

/** A guild-scoped GET, keyed by its path so invalidating the guild refetches it. */
export function useGuildQuery<T>(path: string, options: { enabled?: boolean; refetchInterval?: number | false } = {}) {
  const guildId = useGuildId()
  return useQuery({
    queryKey: [...guildKey(guildId), path],
    queryFn: () => api<T>('GET', `/api/guilds/${guildId}${path}`),
    enabled: !!guildId && options.enabled !== false,
    refetchInterval: options.refetchInterval,
  })
}
