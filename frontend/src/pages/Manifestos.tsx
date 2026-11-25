import { useGuildQuery } from '../api/hooks'
import type { Schemas } from '../api/types'
import { ConfigPanel } from '../components/ConfigPanel'
import { MemberName, useMemberFilter } from '../components/Names'
import { Badge, Card, Empty, Loaded, PageHeader } from '../components/ui'
import { Time } from '../components/Time'
import { AddWisdom, LineActions } from './actions/ManifestoActions'

export function Manifestos() {
  const member = useMemberFilter()
  const data = useGuildQuery<Schemas['ManifestoOut'][]>('/manifestos')
  return (
    <>
      <PageHeader title="Manifestos" />
      <Loaded query={data}>
        {(manifestos) => {
          const shown = member ? manifestos.filter((m) => m.author_ids.includes(member)) : manifestos
          if (shown.length === 0) return <Empty>No manifestos{member ? ' for this member' : ''}.</Empty>
          return (
            <div className="space-y-4">
              {shown.map((m) => (
                <Card
                  key={m.author_ids.join(',')}
                  title={
                    <span className="flex flex-wrap items-center gap-2">
                      {m.author_ids.map((id) => (
                        <MemberName key={id} id={id} />
                      ))}
                    </span>
                  }
                >
                  <ol className="space-y-1 text-sm">
                    {m.lines.map((line) => (
                      <li key={line.index} className="group flex items-start gap-2 rounded px-1 hover:bg-stone-50">
                        <span className="w-8 shrink-0 text-right text-stone-400 tabular-nums">{line.index}.</span>
                        <span className="flex-1">{line.text}</span>
                        <span className="flex shrink-0 flex-wrap items-center gap-1 text-xs text-stone-500">
                          {line.from_file ? (
                            <Badge>from the file</Badge>
                          ) : (
                            <>
                              added by <MemberName id={line.added_by} /> <Time value={line.added_at} />
                              {line.edited_at && <Badge color="amber">edited</Badge>}
                              <LineActions authorId={line.added_by ?? m.author_ids[0]} line={line} />
                            </>
                          )}
                        </span>
                      </li>
                    ))}
                  </ol>
                  <AddWisdom authorId={m.author_ids[0]} />
                </Card>
              ))}
            </div>
          )
        }}
      </Loaded>
      <ConfigPanel section="manifesto" />
    </>
  )
}
