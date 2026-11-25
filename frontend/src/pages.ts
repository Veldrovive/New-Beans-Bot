// Every page in the left nav. `features` are the guild config sections it needs; a guild with none of
// them gets the entry dimmed and a "Not set up for this server" page.
export type PageEntry = { path: string; label: string; features?: string[] }

export const PAGES: PageEntry[] = [
  { path: '', label: 'Overview' },
  { path: 'members', label: 'Members' },
  { path: 'coins', label: 'Coins', features: ['coins'] },
  { path: 'jail', label: 'Jail', features: ['jail'] },
  { path: 'markets', label: 'Markets', features: ['gambling'] },
  { path: 'auto-bets', label: 'Auto bets', features: ['auto_bets'] },
  { path: 'vip', label: 'VIP', features: ['vip'] },
  { path: 'snipes', label: 'Snipes', features: ['snipes'] },
  { path: 'heists', label: 'Heists', features: ['heists'] },
  { path: 'manifestos', label: 'Manifestos', features: ['manifesto'] },
  { path: 'daily-posts', label: 'Daily posts', features: ['birthday', 'action_holiday'] },
  { path: 'fat-cow', label: 'Fat cow', features: ['fat_cow'] },
  { path: 'role-polls', label: 'Role polls', features: ['role_name'] },
  { path: 'classifier', label: 'Classifier', features: ['classifier'] },
]

export function hasFeature(features: Record<string, boolean> | undefined, page: PageEntry): boolean {
  return !page.features || page.features.some((f) => features?.[f])
}
