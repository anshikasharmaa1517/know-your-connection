import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Avatar } from '../components/Ring'
import { Highlight, Pager, Spinner, usePage, useToast } from '../components/ui'
import { PersonSheet } from '../components/PersonSheet'
import { openFriends } from '../components/Friends'
import type { Tab } from '../App'

export type Hop = { name: string; kind: 'friend' | 'contact' }
type Hit = {
  key: string
  person_id: string | null
  name: string
  position: string
  company: string
  url: string
  degree: number
  strength: string | null
  paths: Hop[][]
  matched: string[]
  note: string | null
}
type Result = {
  query: string
  label: string
  plan: 'free' | 'pro'
  scanned: { own: number; friends: number; fof: number }
  first: Hit[]
  second: Hit[]
  third: { locked: boolean; networks: number; people: Hit[] }
}

const SUGGESTIONS = ['SDE', 'Product manager', 'Recruiter', 'VC', 'Hiring intern', 'Wants a cofounder']

/** Words in a note that match the search, for highlighting ("hiring" matches "hire"). */
function noteWords(note: string, query: string): string[] {
  const stem = (w: string) => w.toLowerCase().replace(/(ing|ers|er|ed|es|s|e)$/, '')
  const want = query.split(/\s+/).filter((w) => w.length > 2).map(stem).filter((w) => w.length > 2)
  return note.split(/[^A-Za-z0-9-]+/).filter((w) => want.some((q) => stem(w).startsWith(q)))
}

export function pathText(path: Hop[]): string {
  if (!path.length) return ''
  const [first, ...rest] = path
  const head = first.kind === 'friend' ? first.name : `${first.name}, one of your contacts`
  return rest.length ? `Through ${head}, then ${rest.map((h) => h.name).join(', then ')}` : `Through ${head}`
}


export function FindView({
  go: _go,
  myName,
  plan,
  onPlan,
}: {
  go: (t: Tab) => void
  myName: string
  plan: 'free' | 'pro'
  onPlan: (p: 'free' | 'pro') => void
}) {
  const [q, setQ] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [scanning, setScanning] = useState(false)
  const [deep, setDeep] = useState(false)
  const [deepDone, setDeepDone] = useState(false)
  const toast = useToast()
  const run = useRef(0)

  const search = async (query: string, depth = 2) => {
    const term = query.trim()
    if (!term) return
    const id = ++run.current
    if (depth === 3) setDeep(true)
    else {
      setScanning(true)
      setResult(null)
      setDeepDone(false)
    }
    try {
      // Keep the scan on screen long enough to read, even when the graph answers instantly.
      const [r] = await Promise.all([
        api<Result>(`/api/find?q=${encodeURIComponent(term)}&depth=${depth}`),
        new Promise((ok) => window.setTimeout(ok, depth === 3 ? 700 : 1100)),
      ])
      if (id === run.current) {
        setResult(r)
        if (depth === 3 && !r.third.locked) setDeepDone(true)
      }
    } catch (e: any) {
      toast(e.message)
    } finally {
      if (id === run.current) {
        setScanning(false)
        setDeep(false)
      }
    }
  }

  const unlock = async () => {
    const s = await api<{ plan: 'free' | 'pro' }>('/api/settings', { method: 'PATCH', body: { plan: 'pro' } })
    onPlan(s.plan)
    if (result) search(result.query, 3)
  }

  const total = result ? result.first.length + result.second.length : 0

  return (
    <div className="view">
      <div className="view-head">
        <h1>Find someone</h1>
        <p className="muted">Search by role, or by anything you noted about someone. People you know come first, then people your friends know. No request needed.</p>
      </div>

      <form
        className="find-form"
        onSubmit={(e) => {
          e.preventDefault()
          search(q)
        }}
      >
        <input
          className="input"
          type="search"
          placeholder="SDE, product manager, recruiter at Stackline..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Role to find"
        />
        <button className="btn btn-primary" disabled={!q.trim() || scanning}>
          Find
        </button>
      </form>
      {!result && !scanning && (
        <div className="row row-wrap">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              className="chip chip-button"
              onClick={() => {
                setQ(s)
                search(s)
              }}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {scanning && <Scan query={q} />}

      {result && !scanning && (
        <div className="stack find-results">
          <p className="small muted">
            {total === 0 ? 'No matches' : `${total} match${total === 1 ? '' : 'es'}`} for <strong>{result.label || result.query}</strong>, from
            your graph{result.scanned.friends ? ` and ${result.scanned.friends === 1 ? "1 friend's graph" : `${result.scanned.friends} friends' graphs`}` : ''}.
          </p>

          <Degree
            n={1}
            title="1st degree"
            subtitle="You know them"
            hits={result.first}
            empty="No one you know directly fits this yet."
            myName={myName}
            query={result.query}
          />
          <Degree
            n={2}
            title="2nd degree"
            subtitle="Your friends know them"
            hits={result.second}
            empty={
              result.scanned.friends
                ? "None of your friends' contacts fit this."
                : 'Add friends from the account menu to search the people they know.'
            }
            myName={myName}
            query={result.query}
          />

          {result.scanned.friends === 0 && (
            <button className="btn btn-quiet btn-small" onClick={openFriends}>
              Add a friend to search their contacts
            </button>
          )}

          <section className="degree">
            <div className="degree-head">
              <span className="degree-num degree-3">3</span>
              <div className="grow">
                <h2>3rd degree</h2>
                <p className="small muted">Your friends' friends know them</p>
              </div>
              <span className="chip">Pro</span>
            </div>
            {deep ? (
              <div className="row small muted">
                <Spinner label="Searching 3rd degree" /> Walking {result.third.networks} more networks...
              </div>
            ) : result.third.people.length > 0 ? (
              <HitList hits={result.third.people} myName={myName} query={result.query} />
            ) : plan !== 'pro' ? (
              <div className="panel-sunk stack locked">
                <p>
                  A 3rd degree search walks {result.third.networks || 'many'} more network{result.third.networks === 1 ? '' : 's'}, so it is part
                  of Pro.
                </p>
                <div className="row row-wrap">
                  <button className="btn btn-primary btn-small" onClick={unlock}>
                    Try Pro
                  </button>
                  <span className="tiny faint">Demo plan switch, no payment.</span>
                </div>
              </div>
            ) : deepDone ? (
              <p className="small muted">Nobody further out fits this either.</p>
            ) : (
              <button className="btn btn-quiet btn-small" onClick={() => search(result.query, 3)}>
                Search 3rd degree
              </button>
            )}
          </section>
        </div>
      )}
    </div>
  )
}

function Degree({
  n,
  title,
  subtitle,
  hits,
  empty,
  myName,
  query,
}: {
  query: string
  n: number
  title: string
  subtitle: string
  hits: Hit[]
  empty: string
  myName: string
}) {
  return (
    <section className="degree">
      <div className="degree-head">
        <span className={`degree-num degree-${n}`}>{n}</span>
        <div className="grow">
          <h2>{title}</h2>
          <p className="small muted">{subtitle}</p>
        </div>
        {hits.length > 0 && <span className="chip">{hits.length}</span>}
      </div>
      {hits.length ? <HitList hits={hits} myName={myName} query={query} /> : <p className="small muted">{empty}</p>}
    </section>
  )
}

function HitList({ hits, query }: { hits: Hit[]; myName: string; query: string }) {
  const [open, setOpen] = useState<string | null>(null)
  const pg = usePage(hits)
  const shown = pg.items
  return (
    <div>
      {shown.map((h) => {
        const tie = h.degree === 1 ? (h.strength === 'close' ? 'close' : 'known') : h.degree === 2 ? 'via' : 'none'
        return (
          <div key={h.key} className="person-row hit">
            <Avatar name={h.name} tie={tie} size={40} />
            <span className="grow">
              {h.person_id ? (
                <button className="link-button person-name hit-name" onClick={() => setOpen(h.person_id)}>
                  {h.name}
                </button>
              ) : (
                <span className="person-name">{h.name}</span>
              )}
              <span className="small muted ellipsis" style={{ display: 'block' }}>
                <Highlight text={[h.position, h.company].filter(Boolean).join(' at ')} words={h.matched} />
              </span>
              {h.note && (
                <span className="hit-note small">
                  Your note: <Highlight text={h.note} words={noteWords(h.note, query)} />
                </span>
              )}
              {h.degree > 1 && (
                <span className="tiny faint" style={{ display: 'block' }}>
                  {pathText(h.paths[0])}
                  {h.paths.length > 1 ? ` and ${h.paths.length - 1} more` : ''}
                </span>
              )}
            </span>
            <span className="hit-actions">
              {h.url && (
                <a className="btn btn-ghost btn-small" href={h.url} target="_blank" rel="noreferrer" aria-label={`${h.name} on LinkedIn`}>
                  in
                </a>
              )}
            </span>
          </div>
        )
      })}
      {open && <PersonSheet id={open} onClose={() => setOpen(null)} />}
      <Pager {...pg} />
    </div>
  )
}

const STAGES = ['Your contacts', "Your friends' contacts", 'Ordering by degree']

export function Scan({
  query,
  stages = STAGES,
  label = 'Looking for',
  cycle,
}: {
  query: string
  stages?: string[]
  label?: string
  /** Several things being looked for: shown one after another while the scan runs. */
  cycle?: string[]
}) {
  const [turn, setTurn] = useState(0)
  useEffect(() => {
    if (!cycle || cycle.length < 2) return
    const t = window.setInterval(() => setTurn((n) => n + 1), 800)
    return () => window.clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cycle?.length])
  const shown = cycle && cycle.length ? cycle[turn % cycle.length] : query
  const [stage, setStage] = useState(0)
  useEffect(() => {
    const t = window.setInterval(() => setStage((s) => Math.min(s + 1, stages.length - 1)), 420)
    return () => window.clearInterval(t)
  }, [])
  return (
    <div className="scan" role="status" aria-live="polite">
      <div className="scan-rings" aria-hidden>
        <span className="scan-ring scan-ring-3" />
        <span className="scan-ring scan-ring-2" />
        <span className="scan-ring scan-ring-1" />
        <span className="scan-sweep" />
        <span className="scan-me" />
        {[0, 1, 2, 3, 4, 5, 6].map((i) => (
          <span key={i} className={`scan-dot scan-dot-${i}`} />
        ))}
      </div>
      <p className="scan-label">
        <span className="scan-fixed">{label}</span>
        <strong key={shown} className="scan-word">
          {shown.trim()}
        </strong>
      </p>
      <ol className="scan-stages">
        {stages.map((s, i) => (
          <li key={s} className={i < stage ? 'done' : i === stage ? 'now' : ''}>
            {s}
          </li>
        ))}
      </ol>
    </div>
  )
}
