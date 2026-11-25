// Short names for the generated API types. Regenerate schema.d.ts with `npm run gen:api`.
import type { components } from './schema'

export type Schemas = components['schemas']
export type Meta = Schemas['Meta']
export type GuildSummary = Schemas['GuildSummary']
export type Directory = Schemas['Directory']
export type Amount = { value: number; text: string }
