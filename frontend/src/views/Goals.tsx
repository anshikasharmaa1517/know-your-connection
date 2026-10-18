import { useCallback, useEffect, useRef, useState } from 'react'
import { api, formatDate } from '../api'
import { Avatar, Meter } from '../components/Ring'
import { Spinner, useToast } from '../components/ui'
import { PersonSheet } from '../components/PersonSheet'
import { NetworkMap, type AgentRun } from '../components/NetworkMap'
import type { Tab } from '../App'
import { openFriends } from '../components/Friends'
import { pathText, Scan, type Hop } from './Find'

type GoalRow = { goal: { id: string; text: string; status: string; created_at: number }; people: number; gaps: number }

type Receipt = { kind: string; text: string; source?: string; source_kind?: string; at?: number }
type Ranked = {
  person: { id: string; name: string; headline: string }
  score: number
  reason: string
  need: string
  tie: string
  via_name: string
  strength: string | null
  receipts: Receipt[]
}
type Gap = { id: string; need: string; description: string; text: string; best_score: number; request_id?: string; agents?: AgentResult | null }
type GoalDetail = {
  goal: { id: string; text: string; summary: string; status: string; needs: { label: string; description: string; keywords: string[] }[]; pool: number; shortlist: number; ranked_at: number; agents?: AgentRun | null }
  ranking: Ranked[]
  gaps: Gap[]
}

export function GoalsView({ myName }: { go: (t: Tab) => void; myName: string }) {
  const [goals, setGoals] = useState<GoalRow[] | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<GoalDetail | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const [showPast, setShowPast] = useState(false)
  const [view, setView] = useState<'list' | 'map'>('list')
  const [askSignal, setAskSignal] = useState(0)
  // Friends' agent results per goal, shared by the gap cards and the map.
  const [agents, setAgents] = useState<Record<string, AgentRun>>({})
  const saveAgents = useCallback((gid: string, r: AgentRun) => setAgents((a) => ({ ...a, [gid]: r })), [])
  const toast = useToast()
  const pastCount = goals?.filter((g) => g.goal.status === 'closed').length ?? 0

  const loadList = useCallback(async () => {
    const r = await api<{ goals: GoalRow[] }>('/api/goals')
    setGoals(r.goals)
    setSelected((cur) => cur ?? r.goals.find((g) => g.goal.status === 'open')?.goal.id ?? null)
  }, [])

  useEffect(() => {
    loadList().catch((e) => toast(e.message))
  }, [loadList, toast])

  useEffect(() => {
    if (!selected) {
      setDetail(null)
      return
    }
    api<GoalDetail>(`/api/goals/${selected}`).then(setDetail).catch((e) => toast(e.message))
  }, [selected, toast])

  const create = async () => {
    if (!text.trim()) return
    setBusy(true)
    try {
      const d = await api<GoalDetail>('/api/goals', { body: { text } })
      setText('')
      setDetail(d)
      setSelected(d.goal.id)
      loadList()
    } catch (e: any) {
      toast(e.message)
    } finally {
      setBusy(false)
    }
  }

  const closeGoal = async () => {
    if (!detail) return
    setBusy(true)
    try {
      await api(`/api/goals/${detail.goal.id}/close`, { method: 'POST' })
      const r = await api<{ goals: GoalRow[] }>('/api/goals')
      setGoals(r.goals)
      // Move on to the next open goal; a closed one only opens from Past goals.
      setSelected(r.goals.find((g) => g.goal.status === 'open')?.goal.id ?? null)
      toast('Goal closed. Find it under Past goals.')
    } catch (e: any) {
      toast(e.message)
    } finally {
      setBusy(false)
    }
  }

  const act = async (path: string, body?: unknown, method = 'POST') => {
    if (!detail) return
    setBusy(true)
    try {
      const d = await api<GoalDetail>(`/api/goals/${detail.goal.id}${path}`, { method, body })
      setDetail(d)
      setEditing(false)
      loadList()
    } catch (e: any) {
      toast(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="view">
      <div className="view-head">
        <h1>Goals</h1>
        <p className="muted">Say what you're working toward and your network re-ranks around it.</p>
      </div>

      <form
        className={goals && goals.length > 0 ? 'compact-form' : 'stack'}
        onSubmit={(e) => {
          e.preventDefault()
          create()
        }}
      >
        <div className="field" style={{ flex: 1 }}>
          <label htmlFor="goal-text" className={goals && goals.length > 0 ? 'visually-hidden' : ''}>
            New goal
          </label>
          <input
            id="goal-text"
            className="input"
            placeholder={goals && goals.length > 0 ? 'Add another goal' : 'Summer 2027 VC internship'}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
        <button className={`btn ${goals && goals.length > 0 ? 'btn-quiet' : 'btn-primary'}`} disabled={busy || !text.trim()}>
          {busy && text ? <Spinner label="Ranking" /> : goals && goals.length > 0 ? 'Add' : 'Rank my network'}
        </button>
        {pastCount > 0 && (
          <button type="button" className="btn btn-ghost past-toggle" aria-expanded={showPast} onClick={() => setShowPast(!showPast)}>
            {showPast ? 'Hide past goals' : `Past goals (${pastCount})`}
          </button>
        )}
      </form>

      {goals && goals.length > 0 && (() => {
        const current = goals.filter((g) => g.goal.status !== 'closed')
        const past = goals.filter((g) => g.goal.status === 'closed')
        return (
          <>
            {current.length > 1 && (
              <div className="goal-pills" role="group" aria-label="Your goals">
                {current.map((g) => (
                  <button key={g.goal.id} className="goal-pill" aria-pressed={selected === g.goal.id} onClick={() => setSelected(g.goal.id)}>
                    {g.goal.text}
                  </button>
                ))}
              </div>
            )}
            {showPast && past.length > 0 && (
              <div className="panel past-goals">
                {past.map((g) => (
                  <button
                    key={g.goal.id}
                    className="person-row"
                    aria-pressed={selected === g.goal.id}
                    onClick={() => setSelected(g.goal.id)}
                  >
                    <span className="grow">
                      <span className="person-name">{g.goal.text}</span>
                      <span className="tiny faint" style={{ display: 'block' }}>
                        Closed. Set {formatDate(g.goal.created_at)}
                      </span>
                    </span>
                    {selected === g.goal.id && <span className="chip">Viewing</span>}
                  </button>
                ))}
              </div>
            )}
          </>
        )
      })()}

      {detail && (
        <section className="stack" aria-label={detail.goal.text}>
          <div className="panel stack">
            {editing ? (
              <form
                className="stack"
                onSubmit={(e) => {
                  e.preventDefault()
                  const v = (e.currentTarget.elements.namedItem('t') as HTMLInputElement).value
                  act('', { text: v }, 'PATCH')
                }}
              >
                <input name="t" className="input" defaultValue={detail.goal.text} aria-label="Goal" autoFocus />
                <div className="row">
                  <button className="btn btn-primary btn-small" disabled={busy}>
                    Save and re-rank
                  </button>
                  <button type="button" className="btn btn-ghost btn-small" onClick={() => setEditing(false)}>
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <>
                <h2>{detail.goal.text}</h2>
                {detail.goal.status === 'open' ? (
                  <>
                    {!(detail.goal.needs.length === 1 && detail.goal.needs[0].label.toLowerCase() === detail.goal.text.toLowerCase()) && (
                      <div className="stack" style={{ gap: 6 }}>
                        <span className="small muted">Kinds of people who'd help</span>
                        <div className="needs">
                          {detail.goal.needs.map((n) => (
                            <span className="chip" key={n.label} title={n.description}>
                              {n.label}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    <p className="tiny faint">
                      Looked at {detail.goal.pool} people{detail.goal.ranked_at ? `, ranked ${formatDate(detail.goal.ranked_at)}` : ''}
                    </p>
                    <div className="row row-wrap">
                      <button className="btn btn-quiet btn-small" onClick={() => setEditing(true)} disabled={busy}>
                        Change goal
                      </button>
                      <button className="btn btn-quiet btn-small" onClick={() => act('/rerank')} disabled={busy}>
                        {busy ? <Spinner /> : 'Re-rank'}
                      </button>
                      <button className="btn btn-danger btn-small" onClick={closeGoal} disabled={busy}>
                        Close goal
                      </button>
                    </div>
                  </>
                ) : (
                  <div className="stack" style={{ gap: 10 }}>
                    <p className="muted">Closed. Its ranking was cleared.</p>
                    <div className="row">
                      <button
                        className="btn btn-quiet btn-small"
                        disabled={busy}
                        onClick={async () => {
                          await act('/reopen')
                          setShowPast(false)
                        }}
                      >
                        {busy ? <Spinner label="Re-ranking" /> : 'Reopen and re-rank'}
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>

          {detail.goal.status === 'open' && (
            <div className="goal-toolbar">
              <span className="small muted">
                {detail.ranking.length === 0
                  ? 'No one you know fits yet'
                  : `${detail.ranking.length} ${detail.ranking.length === 1 ? 'person fits' : 'people fit'} this goal`}
              </span>
              {detail.ranking.length > 0 && (
                <div className="segmented" role="tablist" aria-label="View">
                  <button role="tab" aria-selected={view === 'list'} onClick={() => setView('list')}>
                    List
                  </button>
                  <button role="tab" aria-selected={view === 'map'} onClick={() => setView('map')}>
                    Map
                  </button>
                </div>
              )}
              <button className="btn btn-primary btn-small ask-btn" onClick={() => setAskSignal((n) => n + 1)}>
                Ask your network
              </button>
            </div>
          )}


          {detail.goal.status === 'open' && view === 'map' && detail.ranking.length > 0 && (
            <NetworkMap
              key={`${detail.goal.id}-${detail.goal.ranked_at}`}
              goalId={detail.goal.id}
              onOpen={setOpen}
              agents={agents[detail.goal.id] ?? detail.goal.agents ?? undefined}
              onAgents={(r) => saveAgents(detail.goal.id, r)}
            />
          )}

          {detail.goal.status === 'open' && (view === 'list' || detail.ranking.length === 0) && (
            <div className="panel" style={{ paddingBlock: 4 }}>
              {detail.ranking.length === 0 ? (
                <p className="muted" style={{ padding: '12px 0' }}>
                  No one in your network fits yet. Keep more connections, add notes to people, or ask your network.
                </p>
              ) : (
                <ol className="list">
                  {detail.ranking.map((r) => (
                    <li className="rank" key={r.person.id}>
                      <Meter value={r.score} size={48} label={String(r.score)} tone={r.score >= 70 ? 'keep' : 'ink'} />
                      <div className="stack" style={{ gap: 4 }}>
                        <button className="rank-name" onClick={() => setOpen(r.person.id)}>
                          <strong>{r.person.name}</strong>
                          {r.person.headline && (
                            <span className="small muted" style={{ display: 'block' }}>
                              {r.person.headline}
                            </span>
                          )}
                        </button>
                        <div className="row row-wrap" style={{ gap: 6 }}>
                          {r.strength === 'close' && <span className="chip chip-close">Inner circle</span>}
                          {r.via_name && (
                            <span className="chip">
                              <Avatar name={r.via_name} tie="via" size={18} /> Warm path {r.tie}
                            </span>
                          )}
                        </div>
                        <p>{r.reason}</p>
                        <div className="receipts" aria-label="Receipts">
                          {r.receipts.map((rc, i) => (
                            <div className="receipt" key={i}>
                              {rc.text}
                              <div className="receipt-src">
                                {rc.source_kind === 'linkedin' ? 'LinkedIn export' : rc.source_kind === 'memo' ? `Memo: ${rc.source}` : rc.source}
                                {rc.at ? `, ${formatDate(rc.at)}` : ''}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}

          <div id="friends-results" />
          {detail.goal.status === 'open' && (
            <GapAsk
              key={detail.goal.id}
              goalId={detail.goal.id}
              needs={(detail.gaps.length ? detail.gaps.map((g) => g.need) : detail.goal.needs.map((n) => n.label))}
              myName={myName}
              signal={askSignal}
              lookingFor={lookingFor(detail.goal.needs)}
              showResults={view === 'list' || detail.ranking.length === 0}
              saved={(agents[detail.goal.id] as unknown as AgentResult) ?? (detail.goal.agents as unknown as AgentResult) ?? null}
              onResult={(r) => saveAgents(detail.goal.id, r)}
            />
          )}
        </section>
      )}

      {goals && goals.length === 0 && !detail && (
        <p className="small muted">
          Try "find a hardware mentor", "first check for my climate startup" or "summer 2027 VC internship".
        </p>
      )}
      {open && <PersonSheet id={open} onClose={() => setOpen(null)} />}
    </div>
  )
}


type AgentHit = {
  key: string
  name: string
  position: string
  company: string
  headline: string
  url: string
  degree: number
  paths: Hop[][]
  score: number
  reason: string
  need: string
  for_gap: boolean
}
type AgentResult = {
  need: string
  plan: 'free' | 'pro'
  friends: { name: string; handle: string }[]
  scanned: { friends: number; fof: number }
  third: { locked: boolean; networks: number }
  people: AgentHit[]
  at?: number
}

/** "Ask your network": friends' agents search their own graphs for this gap, right away. */
function GapAsk({
  goalId,
  needs,
  saved,
  signal,
  lookingFor: cycleWords,
  showResults,
  onResult,
}: {
  goalId: string
  needs: string[]
  myName: string
  saved: AgentResult | null
  signal: number
  lookingFor: string[]
  showResults: boolean
  onResult: (r: AgentRun) => void
}) {
  // Results from the last time you asked are kept with the goal, so they are here when you come back.
  const [state, setState] = useState<'idle' | 'running' | 'done'>(saved ? 'done' : 'idle')
  const [result, setResult] = useState<AgentResult | null>(saved)
  const toast = useToast()

  const run = async (depth = 2) => {
    setState('running')
    try {
      const [r] = await Promise.all([
        api<AgentResult>(`/api/goals/${goalId}/ask?depth=${depth}`, { method: 'POST' }),
        new Promise((ok) => window.setTimeout(ok, 1200)),
      ])
      setResult(r)
      onResult(r as unknown as AgentRun)
      setState('done')
    } catch (e: any) {
      toast(e.message)
      setState(result ? 'done' : 'idle')
    }
  }

  const [hidden, setHidden] = useState(false)
  const first = useRef(signal)
  useEffect(() => {
    if (signal === first.current) return
    first.current = signal
    setHidden(false)
    run()
    // Results sit under your own list, so bring them into view.
    window.setTimeout(() => document.getElementById('friends-results')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signal])

  const tryPro = async () => {
    await api('/api/settings', { method: 'PATCH', body: { plan: 'pro' } })
    run(3)
  }

  if (state === 'idle' || hidden) return null
  // In Map view the results are drawn on the map itself; only the scan shows here.
  if (!showResults && state === 'done') return null
  if (state === 'running') {
    return <Scan query={needs[0] ?? 'people'} cycle={cycleWords.length ? cycleWords : needs} label="Friends' agents looking for" stages={['Waking your friends\' agents', 'Each searches its own network', 'Scoring who fits']} />
  }
  if (!result) return null

  if (result.scanned.friends === 0) {
    return (
      <div className="stack" style={{ gap: 8 }}>
        <p className="small">You have no friends added yet, so there are no agents to ask.</p>
        <button className="btn btn-quiet btn-small" onClick={openFriends}>
          Add a friend
        </button>
      </div>
    )
  }

  // Name friends by handle when first names clash.
  const firsts = result.friends.map((f) => f.name.split(' ')[0].toLowerCase())
  const label = (f: { name: string; handle: string }) => {
    const first = f.name.split(' ')[0]
    return firsts.filter((x) => x === first.toLowerCase()).length > 1 ? `${first} (${f.handle})` : first
  }
  const gapNeeds = new Set(needs.map((n) => n.toLowerCase()))
  const forGap = result.people.filter((p) => p.for_gap || gapNeeds.has((p.need || '').toLowerCase()))
  const forGoal = result.people.filter((p) => !forGap.includes(p))
  const finders = (list: AgentHit[]) => {
    const counts: Record<string, number> = {}
    list.forEach((p) => {
      const hop = p.paths[0]?.[0] as (Hop & { handle?: string }) | undefined
      if (hop?.kind === 'friend') counts[hop.handle ?? hop.name] = (counts[hop.handle ?? hop.name] ?? 0) + 1
    })
    return result.friends
      .filter((f) => counts[f.handle])
      .map((f) => `${label(f)}'s agent found ${counts[f.handle]}`)
      .join(', ')
  }
  const summary =
    result.people.length === 0 ? "Nobody in your friends' networks fits this yet." : `${finders(result.people)}.`
  const ordered = [...forGap, ...forGoal]

  const row = (h: AgentHit, showNeed: boolean) => {
    return (
      <div className="person-row hit" key={h.key}>
        <Avatar name={h.name} tie={h.degree === 2 ? 'via' : 'none'} size={40} />
        <span className="grow">
          <span className="person-name">{h.name}</span>
          <span className="small muted" style={{ display: 'block' }}>
            {[h.position, h.company].filter(Boolean).join(' at ') || h.headline}
          </span>
          {showNeed && <span className="chip need-chip">{h.need}</span>}
          <span className="small" style={{ display: 'block' }}>
            {h.reason}
          </span>
          <span className="tiny faint" style={{ display: 'block' }}>
            {pathText(h.paths[0] ?? [])}
          </span>
        </span>
        <span className="hit-actions">
          <Meter value={h.score} size={36} label={String(h.score)} tone={h.score >= 70 ? 'keep' : 'ink'} />
          {h.url && (
            <a className="btn btn-ghost btn-small" href={h.url} target="_blank" rel="noreferrer" aria-label={`${h.name} on LinkedIn`}>
              in
            </a>
          )}
        </span>
      </div>
    )
  }

  return (
    <div className="panel stack agent-results" style={{ gap: 8 }}>
      <div className="section-title">From your friends' networks</div>
      <p className="small">
        <strong>{summary}</strong>
        {result.people.length > 0 && ' Only their public profile was used, never their notes.'}
      </p>
      {ordered.map((h) => row(h, true))}
      <div className="row row-wrap">
        {result.scanned.fof > 0 ? null : result.plan === 'pro' ? (
          result.third.networks > 0 && (
            <button className="btn btn-quiet btn-small" onClick={() => run(3)}>
              Also ask friends of friends
            </button>
          )
        ) : (
          result.third.networks > 0 && (
            <>
              <button className="btn btn-quiet btn-small" onClick={tryPro}>
                Ask friends of friends (Pro)
              </button>
              <span className="tiny faint">{result.third.networks} more networks. Demo plan switch, no payment.</span>
            </>
          )
        )}
        <button className="btn btn-ghost btn-small" onClick={() => run(result.scanned.fof > 0 ? 3 : 2)}>
          Ask again
        </button>
        {result.at && <span className="tiny faint">Asked {formatWhen(result.at)}</span>}
        <button className="btn btn-ghost btn-small" onClick={() => setHidden(true)}>
          Hide
        </button>
      </div>
    </div>
  )
}


function formatWhen(ms: number): string {
  const d = new Date(ms)
  const sameDay = d.toDateString() === new Date().toDateString()
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  return sameDay ? `today at ${time}` : `${formatDate(ms)} at ${time}`
}


const VAGUE = new Set(['remote', 'work', 'job', 'jobs', 'role', 'roles', 'startup', 'company', 'team', 'senior', 'lead', 'manager', 'leader', 'hiring', 'people'])

/** What the scan says it is looking for: the needs, then the role words behind them, shuffled. */
function lookingFor(needs: { label: string; keywords: string[] }[]): string[] {
  const labels = needs.map((n) => n.label)
  const words = needs.flatMap((n) => n.keywords).filter((k) => k && !VAGUE.has(k.toLowerCase()) && k.length > 1)
  const seen = new Set<string>()
  const all = [...labels, ...words].filter((w) => {
    const k = w.toLowerCase()
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
  const rest = all.slice(labels.length).sort(() => Math.random() - 0.5)
  const out: string[] = []
  labels.forEach((l, i) => {
    out.push(l)
    if (rest[i]) out.push(rest[i])
  })
  return [...out, ...rest.slice(labels.length)].slice(0, 10)
}
