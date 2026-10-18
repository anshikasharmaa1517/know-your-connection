import { animate, motion, useMotionValue, useReducedMotion, useTransform } from 'motion/react'
import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react'
import { api, formatDate } from '../api'
import { Avatar, Meter } from '../components/Ring'
import { Highlight, Spinner, useToast } from '../components/ui'
import type { Tab } from '../App'

export type ImportSummary = {
  id: string
  filename: string
  created_at: number
  total: number
  pending: number
  kept: number
  merged: number
  skipped: number
  known: number
  close: number
}

type Card = {
  id: string
  name: string
  headline: string
  company: string
  position: string
  url: string
  email: string
  connected_on: string
  match?: { id: string; name: string; headline: string; seen_in?: string } | null
  relevance: { goal_id: string; goal: string; hits: string[] }[]
  context: string[]
}

type Action = 'keep' | 'close' | 'skip'

export function ImportView({ go }: { go: (t: Tab) => void }) {
  const [imports, setImports] = useState<ImportSummary[] | null>(null)
  const [active, setActive] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const toast = useToast()

  const load = useCallback(async () => {
    const r = await api<{ imports: ImportSummary[] }>('/api/imports')
    setImports(r.imports)
    setActive((cur) => cur ?? r.imports[0]?.id ?? null)
  }, [])

  useEffect(() => {
    load().catch((e) => setError(e.message))
  }, [load])

  const finishUpload = (r: any) => {
    setActive(r.import.id)
    setAdding(false)
    const known = r.already_known ? ` ${r.already_known} were already sorted.` : ''
    toast(`${r.import.pending} connections ready to sort.${known}`)
    load()
  }

  const upload = async (file: File) => {
    setUploading(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      finishUpload(await api('/api/imports/linkedin', { body: form }))
    } catch (e: any) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }

  const useSample = async (set: 'a' | 'b' = 'a') => {
    setUploading(true)
    setError(null)
    try {
      finishUpload(await api(`/api/imports/sample?set=${set}`, { method: 'POST' }))
    } catch (e: any) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }

  if (imports === null) {
    return <div className="view">{error ? <p className="error">{error}</p> : <Spinner />}</div>
  }

  if (!active || adding) {
    return (
      <div className="view">
        <div className="view-head">
          <h1>{imports.length ? 'Upload a newer export' : 'Bring in your LinkedIn connections'}</h1>
          <p className="muted">
            Upload the file LinkedIn sends you. Each connection becomes a card, and nobody is added to your network until you
            swipe.
          </p>
        </div>
        <ol className="steps panel">
          <li>
            <span>
              On LinkedIn, open <strong>Settings</strong>, then <strong>Data privacy</strong>, then{' '}
              <strong>Get a copy of your data</strong>.
            </span>
          </li>
          <li>
            <span>
              Choose <strong>Connections</strong> and request the archive.
            </span>
          </li>
          <li>
            <span>LinkedIn emails you a download link, usually within minutes.</span>
          </li>
          <li>
            <span>Drop the .zip, or the Connections.csv inside it, below.</span>
          </li>
        </ol>
        <Dropzone onFile={upload} busy={uploading} />
        {error && <p className="error">{error}</p>}
        <div className="row row-wrap spread">
          <span className="row row-wrap">
            <button className="btn btn-ghost" onClick={() => useSample('a')} disabled={uploading}>
              Try it with sample connections
            </button>
            <button className="btn btn-ghost btn-small" onClick={() => useSample('b')} disabled={uploading} title="A different fictional crowd, handy for a second demo account">
              Second sample set
            </button>
          </span>
          {imports.length > 0 && (
            <button className="btn btn-ghost" onClick={() => setAdding(false)}>
              Back to sorting
            </button>
          )}
        </div>
        <p className="tiny faint">
          Only the file you upload is read. We never sign in to LinkedIn or look at anyone else's profile.
        </p>
      </div>
    )
  }

  return <SwipeDeck key={active} importId={active} go={go} onNewer={() => setAdding(true)} />
}

function Dropzone({ onFile, busy }: { onFile: (f: File) => void; busy: boolean }) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  return (
    <div
      className={`dropzone ${over ? 'dropzone-over' : ''}`}
      onClick={() => input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        const f = e.dataTransfer.files[0]
        if (f) onFile(f)
      }}
    >
      {busy ? <Spinner label="Reading your export" /> : <Meter value={0} size={40} stroke={3} />}
      <strong>{busy ? 'Reading your export' : 'Choose your LinkedIn export'}</strong>
      <span className="small muted">.zip or Connections.csv</span>
      <input
        ref={input}
        type="file"
        accept=".zip,.csv,text/csv,application/zip"
        className="visually-hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onFile(f)
          e.target.value = ''
        }}
      />
    </div>
  )
}

function SwipeDeck({ importId, go, onNewer }: { importId: string; go: (t: Tab) => void; onNewer: () => void }) {
  const [cards, setCards] = useState<Card[]>([])
  const [summary, setSummary] = useState<ImportSummary | null>(null)
  const [goals, setGoals] = useState<{ id: string; text: string }[]>([])
  const [loading, setLoading] = useState(true)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [menu, setMenu] = useState(false)
  const decided = useRef(new Set<string>())
  const fly = useRef<((a: Action) => void) | null>(null) as MutableRefObject<((a: Action) => void) | null>
  const fetching = useRef(false)
  const toast = useToast()

  const fetchDeck = useCallback(async () => {
    if (fetching.current) return
    fetching.current = true
    try {
      const d = await api(`/api/imports/${importId}/deck?limit=12`)
      setSummary(d.import)
      setGoals(d.goals)
      setCards((cur) => {
        const have = new Set(cur.map((c) => c.id))
        const fresh = (d.cards as Card[]).filter((c) => !decided.current.has(c.id) && !have.has(c.id))
        return [...cur, ...fresh]
      })
    } finally {
      fetching.current = false
      setLoading(false)
    }
  }, [importId])

  useEffect(() => {
    fetchDeck().catch((e) => toast(e.message))
  }, [fetchDeck, toast])

  const decide = useCallback(
    async (card: Card, action: Action) => {
      decided.current.add(card.id)
      setCards((cs) => cs.filter((c) => c.id !== card.id))
      try {
        const r = await api(`/api/imports/${importId}/cards/${card.id}`, { body: { action } })
        setSummary(r.import)
        if (r.import.pending > 0) {
          setCards((cs) => {
            if (cs.length <= 3) fetchDeck()
            return cs
          })
        }
      } catch (e: any) {
        decided.current.delete(card.id)
        setCards((cs) => [card, ...cs])
        toast(e.message)
      }
    },
    [importId, fetchDeck, toast],
  )

  const undo = useCallback(async () => {
    try {
      const r = await api(`/api/imports/${importId}/undo`, { method: 'POST' })
      decided.current.delete(r.card.id)
      setCards((cs) => [r.card, ...cs.filter((c) => c.id !== r.card.id)])
      setSummary(r.import)
      toast(`${r.card.name} is back in the deck`)
    } catch (e: any) {
      toast(e.message)
    }
  }, [importId, toast])

  const bulk = async (action: 'keep' | 'skip') => {
    const left = summary?.pending ?? 0
    const verb = action === 'keep' ? 'Keep' : 'Skip'
    if (!window.confirm(`${verb} all ${left} remaining connections?`)) return
    setMenu(false)
    setBulkBusy(true)
    try {
      const r = await api(`/api/imports/${importId}/bulk`, { body: { action } })
      setSummary(r.import)
      setCards([])
      toast(`${action === 'keep' ? 'Kept' : 'Skipped'} ${r.changed} connections`)
    } catch (e: any) {
      toast(e.message)
    } finally {
      setBulkBusy(false)
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const map: Record<string, Action> = { ArrowRight: 'keep', ArrowLeft: 'skip', ArrowUp: 'close' }
      if (map[e.key] && fly.current) {
        e.preventDefault()
        fly.current(map[e.key])
      } else if (e.key === 'z' || e.key === 'Backspace') {
        e.preventDefault()
        undo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [undo])

  if (loading || !summary) return <div className="view"><Spinner /></div>

  const sorted = summary.kept + summary.merged + summary.skipped
  const total = sorted + summary.pending
  const top = cards[0]

  if (!top && summary.pending === 0) {
    return (
      <div className="view">
        <div className="panel stack done">
          <Meter value={1} max={1} size={72} stroke={6} tone="keep" label="Done" />
          <h2>All {total} sorted</h2>
          <div className="tally">
            <div>
              <strong>{summary.kept + summary.merged}</strong>
              <span className="small muted">kept</span>
            </div>
            <div>
              <strong>{summary.close}</strong>
              <span className="small muted">inner circle</span>
            </div>
            <div>
              <strong>{summary.skipped}</strong>
              <span className="small muted">skipped</span>
            </div>
          </div>
          {summary.merged > 0 && (
            <p className="small muted">{summary.merged} merged into people you already had from memos.</p>
          )}
          <div className="row row-wrap" style={{ justifyContent: 'center' }}>
            <button className="btn btn-primary" onClick={() => go('goals')}>
              Set a goal
            </button>
            <button className="btn btn-quiet" onClick={() => go('find')}>
              Find someone
            </button>
          </div>
          <div className="row" style={{ justifyContent: 'center' }}>
            <button className="btn btn-ghost btn-small" onClick={undo}>
              Undo last
            </button>
            <button className="btn btn-ghost btn-small" onClick={onNewer}>
              Upload a newer export
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="view">
      <div className="deck-head">
        <Meter value={sorted} max={total} size={52} stroke={5} tone="keep" label={String(summary.pending)} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2>Sort your connections</h2>
          <p className="small muted">
            {summary.pending} left{goals.length ? '. Goal matches first.' : ''}
          </p>
        </div>
      </div>

      <div className="deck" aria-live="polite">
        {cards[2] && (
          <div className="card card-under-2" aria-hidden>
            <CardFace card={cards[2]} hasGoals={goals.length > 0} />
          </div>
        )}
        {cards[1] && (
          <div className="card card-under" aria-hidden>
            <CardFace card={cards[1]} hasGoals={goals.length > 0} />
          </div>
        )}
        {top ? (
          <TopCard key={top.id} card={top} hasGoals={goals.length > 0} flyRef={fly} onDecide={(a) => decide(top, a)} />
        ) : (
          <div className="card" style={{ placeItems: 'center', justifyContent: 'center' }}>
            <Spinner />
          </div>
        )}
      </div>

      <div className="deck-actions" role="group" aria-label="Decide on this connection">
        <button className="swipe-btn swipe-skip" onClick={() => fly.current?.('skip')} disabled={!top || bulkBusy}>
          <span>Skip</span>
          <span className="key">Left</span>
        </button>
        <button className="swipe-btn swipe-close" onClick={() => fly.current?.('close')} disabled={!top || bulkBusy}>
          <span>Inner circle</span>
          <span className="key">Up</span>
        </button>
        <button className="swipe-btn swipe-keep" onClick={() => fly.current?.('keep')} disabled={!top || bulkBusy}>
          <span>Keep</span>
          <span className="key">Right</span>
        </button>
      </div>

      <div className="deck-foot">
        <button className="btn btn-ghost btn-small" onClick={undo} disabled={sorted === 0}>
          Undo
        </button>
        <div style={{ position: 'relative' }}>
          <button className="btn btn-ghost btn-small" aria-expanded={menu} onClick={() => setMenu((m) => !m)}>
            {bulkBusy ? <Spinner /> : 'More'}
          </button>
          {menu && (
            <div className="panel stack" style={{ position: 'absolute', right: 0, bottom: 44, width: 240, zIndex: 5, gap: 4, padding: 8 }}>
              <button className="btn btn-ghost btn-small" style={{ justifyContent: 'flex-start' }} onClick={() => bulk('keep')}>
                Keep all remaining
              </button>
              <button className="btn btn-ghost btn-small" style={{ justifyContent: 'flex-start' }} onClick={() => bulk('skip')}>
                Skip all remaining
              </button>
              <button className="btn btn-ghost btn-small" style={{ justifyContent: 'flex-start' }} onClick={onNewer}>
                Upload a newer export
              </button>
            </div>
          )}
        </div>
      </div>
      {sorted === 0 && (
        <p className="small muted">
          Swipe right to keep someone, up to put them in your inner circle, left to skip. On a keyboard, use the arrow keys and Z to
          undo.
        </p>
      )}
    </div>
  )
}

function TopCard({
  card,
  hasGoals,
  onDecide,
  flyRef,
}: {
  card: Card
  hasGoals: boolean
  onDecide: (a: Action) => void
  flyRef: MutableRefObject<((a: Action) => void) | null>
}) {
  const x = useMotionValue(0)
  const y = useMotionValue(0)
  const rotate = useTransform(x, [-220, 220], [-12, 12])
  const keepO = useTransform(x, [24, 110], [0, 1])
  const skipO = useTransform(x, [-110, -24], [1, 0])
  const closeO = useTransform(y, [-110, -24], [1, 0])
  const reduce = useReducedMotion()
  const gone = useRef(false)

  const fly = useCallback(
    (action: Action) => {
      if (gone.current) return
      gone.current = true
      const duration = reduce ? 0 : 0.24
      const to = action === 'keep' ? { x: 520, y: 40 } : action === 'skip' ? { x: -520, y: 40 } : { x: 0, y: -620 }
      Promise.all([animate(x, to.x, { duration }), animate(y, to.y, { duration })]).then(() => onDecide(action))
    },
    [onDecide, reduce, x, y],
  )

  useEffect(() => {
    flyRef.current = fly
    return () => {
      if (flyRef.current === fly) flyRef.current = null
    }
  }, [fly, flyRef])

  return (
    <motion.div
      className="card card-top"
      style={{ x, y, rotate }}
      drag
      dragSnapToOrigin
      dragElastic={0.9}
      onDragEnd={(_, info) => {
        const { offset, velocity } = info
        if (offset.y < -100 && Math.abs(offset.y) > Math.abs(offset.x)) fly('close')
        else if (offset.x > 100 || velocity.x > 700) fly('keep')
        else if (offset.x < -100 || velocity.x < -700) fly('skip')
      }}
      aria-label={`${card.name}${card.headline ? `, ${card.headline}` : ''}`}
    >
      <CardFace card={card} hasGoals={hasGoals} />
      <motion.span className="stamp stamp-keep" style={{ opacity: keepO }}>
        Keep
      </motion.span>
      <motion.span className="stamp stamp-skip" style={{ opacity: skipO }}>
        Skip
      </motion.span>
      <motion.span className="stamp stamp-close" style={{ opacity: closeO }}>
        Inner circle
      </motion.span>
    </motion.div>
  )
}

function CardFace({ card, hasGoals }: { card: Card; hasGoals: boolean }) {
  const hits = Array.from(new Set(card.relevance.flatMap((r) => r.hits)))
  const quiet = !card.match && card.relevance.length === 0 && card.context.length === 0
  return (
    <>
      <Avatar name={card.name} tie="none" size={88} />
      <div className="stack" style={{ gap: 6 }}>
        <div className="card-name">{card.name}</div>
        <div className="card-headline">
          {card.headline ? <Highlight text={card.headline} words={hits} /> : 'No title on LinkedIn'}
        </div>
      </div>
      <div className="card-meta">
        {card.connected_on && <span>Connected {formatDate(card.connected_on)}</span>}
        {card.email && <span>{card.email}</span>}
        {card.url && (
          <a href={card.url} target="_blank" rel="noreferrer" onPointerDown={(e) => e.stopPropagation()}>
            LinkedIn profile
          </a>
        )}
      </div>
      <div className="card-why">
        {card.match && (
          <div className="why why-merge">
            <strong>Already in your graph</strong> as {card.match.name}
            {card.match.seen_in ? `, from "${card.match.seen_in}"` : ''}. Keeping adds this to them instead of making a copy.
          </div>
        )}
        {card.relevance.map((r) => (
          <div className="why why-goal" key={r.goal_id}>
            Could help with <strong>{r.goal}</strong>
          </div>
        ))}
        {card.context.map((c) => (
          <div className="why" key={c}>
            {c}
          </div>
        ))}
        {quiet && (
          <div className="why why-quiet">
            {hasGoals ? 'No obvious link to your open goals.' : 'Add a goal and the people who fit it move to the front of the deck.'}
          </div>
        )}
      </div>
    </>
  )
}
