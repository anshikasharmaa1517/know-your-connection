import { useEffect, useRef, useState } from 'react'
import { api, copy } from '../api'
import { Avatar } from '../components/Ring'
import { Highlight, Spinner, useToast } from '../components/ui'
import type { Tab } from '../App'

type Hop = { name: string; kind: 'friend' | 'contact' }
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

const SUGGESTIONS = ['SDE', 'Product manager', 'Recruiter', 'Designer', 'VC', 'Data scientist']
const SHOW = 8

function pathText(path: Hop[]): string {
  if (!path.length) return ''
  const [first, ...rest] = path
  const head = first.kind === 'friend' ? first.name : `${first.name}, one of your contacts`
  return rest.length ? `Through ${head}, then ${rest.map((h) => h.name).join(', then ')}` : `Through ${head}`
}

function introMessage(hit: Hit, myName: string): { to: string; text: string } {
  const to = hit.paths[0]?.[0]?.name ?? ''
  const role = [hit.position, hit.company].filter(Boolean).join(' at ')
  const next = hit.paths[0]?.[1]?.name
  const text = next
    ? `Hi ${to.split(' ')[0]}, could you ask ${next.split(' ')[0]} whether they'd introduce me to ${hit.name}${role ? ` (${role})` : ''}? Happy to send a short blurb to pass along. Thanks, ${myName.split(' ')[0]}`
    : `Hi ${to.split(' ')[0]}, I saw you're connected to ${hit.name}${role ? ` (${role})` : ''}. Would you be open to introducing us? Happy to send a short blurb you can forward. Thanks, ${myName.split(' ')[0]}`
  return { to, text }
}

export function FindView({
  go,
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
        <p className="muted">Search by role. People you know come first, then people your friends know. No request needed.</p>
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
          />
          <Degree
            n={2}
            title="2nd degree"
            subtitle="Your friends know them"
            hits={result.second}
            empty={
              result.scanned.friends
                ? "None of your friends' contacts fit this."
                : 'Add friends on the Ask tab to search the people they know.'
            }
            myName={myName}
          />

          {result.scanned.friends === 0 && (
            <button className="btn btn-quiet btn-small" onClick={() => go('ask')}>
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
              <HitList hits={result.third.people} myName={myName} />
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
}: {
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
      {hits.length ? <HitList hits={hits} myName={myName} /> : <p className="small muted">{empty}</p>}
    </section>
  )
}

function HitList({ hits, myName }: { hits: Hit[]; myName: string }) {
  const [all, setAll] = useState(false)
  const toast = useToast()
  const shown = all ? hits : hits.slice(0, SHOW)
  return (
    <div>
      {shown.map((h) => {
        const tie = h.degree === 1 ? (h.strength === 'close' ? 'close' : 'known') : h.degree === 2 ? 'via' : 'none'
        const intro = h.degree > 1 ? introMessage(h, myName) : null
        return (
          <div key={h.key} className="person-row hit">
            <Avatar name={h.name} tie={tie} size={40} />
            <span className="grow">
              <span className="person-name">{h.name}</span>
              <span className="small muted ellipsis" style={{ display: 'block' }}>
                <Highlight text={[h.position, h.company].filter(Boolean).join(' at ')} words={h.matched} />
              </span>
              {h.degree > 1 && (
                <span className="tiny faint" style={{ display: 'block' }}>
                  {pathText(h.paths[0])}
                  {h.paths.length > 1 ? ` and ${h.paths.length - 1} more` : ''}
                </span>
              )}
              {intro && (
                <button
                  className="link-button small"
                  onClick={async () => toast((await copy(intro.text)) ? `Copied a message for ${intro.to.split(' ')[0]}` : 'Copy failed')}
                >
                  Ask {intro.to.split(' ')[0]} for an intro
                </button>
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
      {hits.length > SHOW && (
        <button className="btn btn-ghost btn-small" onClick={() => setAll(!all)}>
          {all ? 'Show fewer' : `Show all ${hits.length}`}
        </button>
      )}
    </div>
  )
}

const STAGES = ['Your contacts', "Your friends' contacts", 'Ordering by degree']

function Scan({ query }: { query: string }) {
  const [stage, setStage] = useState(0)
  useEffect(() => {
    const t = window.setInterval(() => setStage((s) => Math.min(s + 1, STAGES.length - 1)), 420)
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
        Looking for <strong>{query.trim()}</strong>
      </p>
      <ol className="scan-stages">
        {STAGES.map((s, i) => (
          <li key={s} className={i < stage ? 'done' : i === stage ? 'now' : ''}>
            {s}
          </li>
        ))}
      </ol>
    </div>
  )
}
