import type { ComponentType } from 'react'
import { AutoBets } from './AutoBets'
import { Classifier } from './Classifier'
import { Coins } from './Coins'
import { DailyPosts } from './DailyPosts'
import { FatCow } from './FatCow'
import { Heists } from './Heists'
import { Jail } from './Jail'
import { Manifestos } from './Manifestos'
import { Markets } from './Markets'
import { Members } from './Members'
import { RolePolls } from './RolePolls'
import { Snipes } from './Snipes'
import { Vip } from './Vip'

// Page path (see pages.ts) -> component.
export const PAGE_COMPONENTS: Record<string, ComponentType> = {
  members: Members,
  coins: Coins,
  jail: Jail,
  markets: Markets,
  'auto-bets': AutoBets,
  vip: Vip,
  snipes: Snipes,
  heists: Heists,
  manifestos: Manifestos,
  'daily-posts': DailyPosts,
  'fat-cow': FatCow,
  'role-polls': RolePolls,
  classifier: Classifier,
}
