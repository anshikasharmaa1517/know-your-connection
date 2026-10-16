import { useCallback, useEffect, useState } from 'react'
import { api, formatDate } from '../api'
import { Avatar, Meter } from '../components/Ring'
import { Spinner, useToast } from '../components/ui'
import { PersonSheet } from './People'
import type { AskDraft, Tab } from '../App'

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
type Gap = { id: string; need: string; description: string; text: string; best_score: number; request_id?: string }
type GoalDetail = {
  goal: { id: string; text: string; summary: string; status: string; needs: { label: string; description: string; keywords: string[] }[]; pool: number; shortlist: number; ranked_at: number }
  ranking: Ranked[]
  gaps: Gap[]
}

export function GoalsView({ ask }: { go: (t: Tab) => void; ask: (d: AskDraft) => void }) {
  const [goals, setGoals] = useState<GoalRow[] | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<GoalDetail | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const toast = useToast()

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
      </form>

      {goals && goals.length > 0 && (
        <div className="goal-pills" role="group" aria-label="Your goals">
          {goals.map((g) => (
            <button
              key={g.goal.id}
              className={`goal-pill ${g.goal.status === 'closed' ? 'goal-pill-closed' : ''}`}
              aria-pressed={selected === g.goal.id}
              onClick={() => setSelected(g.goal.id)}
            >
              {g.goal.text}
            </button>
          ))}
        </div>
      )}

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
                      <button className="btn btn-danger btn-small" onClick={() => act('/close')} disabled={busy}>
                        Close goal
                      </button>
                    </div>
                  </>
                ) : (
                  <p className="muted">Closed. Its ranking was cleared.</p>
                )}
              </>
            )}
          </div>

          {detail.gaps.map((gap) => (
            <div className="gap" key={gap.id}>
              <h3>{gap.text}</h3>
              <p className="small muted">{gap.description}</p>
              {gap.request_id ? (
                <p className="small">You asked your network. Check Ask for replies.</p>
              ) : (
                <button
                  className="btn btn-primary"
                  onClick={() =>
                    ask({ need: gap.need, description: gap.description, gap_id: gap.id, goal_id: detail.goal.id, goal: detail.goal.text })
                  }
                >
                  Ask your network
                </button>
              )}
            </div>
          ))}

          {detail.goal.status === 'open' && (
            <div className="panel" style={{ paddingBlock: 4 }}>
              {detail.ranking.length === 0 ? (
                <p className="muted" style={{ padding: '12px 0' }}>
                  No one in your network fits yet. Keep more connections, record a memo, or ask your network.
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
