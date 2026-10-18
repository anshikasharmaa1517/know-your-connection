import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { Spinner } from './ui'

type Node = {
  id: string
  name: string
  headline: string
  company: string
  position: string
  strength: string | null
  direct: boolean
  notes: number
}
type Snapshot = { people: Node[]; ties: { a: string; b: string; relation: string | null }[]; relevant: { id: string; goal_id: string; score: number }[] }
type Friend = { id: string; name: string }

type Placed = Node & { x: number; y: number; angle: number; r: number; ring: 'close' | 'known' | 'via'; via?: string }

const TAU = Math.PI * 2
const R_CLOSE = 110
const R_KNOWN = 215
const BAND = 40

function polar(r: number, a: number) {
  return { x: r * Math.cos(a - Math.PI / 2), y: r * Math.sin(a - Math.PI / 2) }
}

function layout(snap: Snapshot) {
  const close = snap.people.filter((p) => p.direct && p.strength === 'close')
  const known = snap.people
    .filter((p) => p.direct && p.strength !== 'close')
    .sort((a, b) => (a.company || '~').localeCompare(b.company || '~') || a.name.localeCompare(b.name))
  const via = snap.people.filter((p) => !p.direct)

  const placed: Record<string, Placed> = {}
  close.forEach((p, i) => {
    const a = (i / Math.max(close.length, 1)) * TAU + 0.2
    placed[p.id] = { ...p, ...polar(R_CLOSE, a), angle: a, r: R_CLOSE, ring: 'close' }
  })
  // Everyone else you know sits on a band, staggered so large networks still fit,
  // sorted by company so colleagues land next to each other.
  const levels = Math.max(1, Math.ceil(known.length / 36))
  known.forEach((p, i) => {
    const a = (i / Math.max(known.length, 1)) * TAU
    const r = R_KNOWN + (i % levels) * BAND
    placed[p.id] = { ...p, ...polar(r, a), angle: a, r, ring: 'known' }
  })
  const rVia = R_KNOWN + (levels - 1) * BAND + 85
  // People you heard about sit just outside the person you heard it from.
  const byAnchor: Record<string, number> = {}
  via.forEach((p, i) => {
    const tie = snap.ties.find((t) => (t.a === p.id && placed[t.b]?.ring !== 'via' && placed[t.b]) || (t.b === p.id && placed[t.a]))
    const anchorId = tie ? (tie.a === p.id ? tie.b : tie.a) : undefined
    const anchor = anchorId ? placed[anchorId] : undefined
    const k = anchorId ? (byAnchor[anchorId] = (byAnchor[anchorId] ?? -1) + 1) : 0
    const a = anchor ? anchor.angle + (k % 2 ? -1 : 1) * Math.ceil(k / 2) * 0.12 : (i / Math.max(via.length, 1)) * TAU + 0.1
    placed[p.id] = { ...p, ...polar(rVia, a), angle: a, r: rVia, ring: 'via', via: anchorId }
  })

  // Company names for runs of three or more colleagues.
  const groups: { name: string; angle: number }[] = []
  let start = 0
  for (let i = 1; i <= known.length; i++) {
    if (i === known.length || known[i].company !== known[start].company) {
      if (i - start >= 3 && known[start].company) {
        groups.push({ name: known[start].company, angle: (((start + i - 1) / 2) / known.length) * TAU })
      }
      start = i
    }
  }
  return { placed, rVia, levels, groups }
}

function labelProps(p: { x: number; y: number; angle: number }, offset: number) {
  const deg = (p.angle * 180) / Math.PI - 90
  const flip = Math.cos(p.angle - Math.PI / 2) < 0
  const lx = p.x + Math.cos(p.angle - Math.PI / 2) * offset
  const ly = p.y + Math.sin(p.angle - Math.PI / 2) * offset
  return {
    x: lx,
    y: ly,
    transform: `rotate(${flip ? deg + 180 : deg} ${lx} ${ly})`,
    textAnchor: (flip ? 'end' : 'start') as 'end' | 'start',
    dominantBaseline: 'middle' as const,
  }
}

export function NetworkMap({ goalId, onOpen }: { goalId: string; onOpen: (id: string) => void }) {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const friends: Friend[] = []
  const goal = goalId
  const [q, setQ] = useState('')
  const [zoom, setZoom] = useState(1)
  const [hover, setHover] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api<Snapshot>(`/api/graph?goal=${encodeURIComponent(goalId)}`).then((s) => {
      // Only the people who fit this goal, plus anyone they are reached through.
      const fit = new Set(s.relevant.filter((r) => r.goal_id === goalId).map((r) => r.id))
      const keep = new Set(fit)
      s.people.forEach((p) => {
        if (fit.has(p.id) && !p.direct) {
          s.ties.forEach((t) => {
            if (t.a === p.id) keep.add(t.b)
            if (t.b === p.id) keep.add(t.a)
          })
        }
      })
      setSnap({ ...s, people: s.people.filter((p) => keep.has(p.id)) })
    })
  }, [goalId])

  useEffect(() => {
    const el = box.current
    if (el) {
      el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2
      el.scrollTop = (el.scrollHeight - el.clientHeight) / 2
    }
  }, [zoom, snap])

  const lay = useMemo(() => (snap ? layout(snap) : null), [snap])
  if (!snap || !lay) return <Spinner />

  const people = Object.values(lay.placed)
  const hasVia = people.some((p) => p.ring === 'via')
  const rFriends = (hasVia ? lay.rVia : R_KNOWN + (lay.levels - 1) * BAND) + 70
  const R = (friends.length ? rFriends : hasVia ? lay.rVia : rFriends - 40) + 62
  const score: Record<string, number> = {}
  if (goal) snap.relevant.filter((r) => r.goal_id === goal).forEach((r) => (score[r.id] = r.score))
  const term = q.trim().toLowerCase()
  const lit = (p: Node) =>
    (!term || `${p.name} ${p.headline} ${p.company}`.toLowerCase().includes(term))
  const dimming = Boolean(term)
  const showAllLabels = people.length <= 90
  const litCount = people.filter(lit).length
  const hovered = hover ? lay.placed[hover] : null

  if (!people.length) {
    return <p className="muted">No one fits this goal yet, so there is nothing to map.</p>
  }

  return (
    <div className="stack map-wrap">
      <div className="map-controls">
        <input className="input" type="search" placeholder="Highlight by name, role or company" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Highlight people" />
      </div>
      {dimming && (
        <p className="small muted">
          {litCount} of {people.length} people match.
        </p>
      )}

      <div className="map" ref={box}>
        <svg viewBox={`${-R} ${-R} ${2 * R} ${2 * R}`} style={{ width: `${zoom * 100}%` }} role="img" aria-label={`Map of ${people.length} people around you`}>
          {/* rings */}
          <circle r={R_CLOSE} className="map-ring map-ring-close" />
          <circle r={R_KNOWN + ((lay.levels - 1) * BAND) / 2} className="map-ring" style={{ strokeWidth: lay.levels * BAND }} />
          {hasVia && <circle r={lay.rVia} className="map-ring map-ring-via" />}
          {friends.length > 0 && <circle r={rFriends} className="map-ring map-ring-friends" />}

          {lay.groups.map((g) => {
            const p = polar(R_KNOWN - 34, g.angle)
            return (
              <text key={g.name} {...labelProps({ ...p, angle: g.angle }, 0)} className="map-group" textAnchor="middle">
                {g.name}
              </text>
            )
          })}

          {/* edges */}
          {people.map((p) =>
            p.ring === 'via' && p.via && lay.placed[p.via] ? (
              <line key={`e${p.id}`} x1={lay.placed[p.via].x} y1={lay.placed[p.via].y} x2={p.x} y2={p.y} className={`map-edge map-edge-via ${dimming && !lit(p) ? 'dim' : ''}`} />
            ) : p.ring !== 'via' ? (
              <line key={`e${p.id}`} x1={0} y1={0} x2={p.x} y2={p.y} className={`map-edge ${p.ring === 'close' ? 'map-edge-close' : ''} ${dimming && !lit(p) ? 'dim' : ''}`} />
            ) : null,
          )}
          {friends.map((f, i) => {
            const a = (i / friends.length) * TAU + Math.PI / 4
            const p = polar(rFriends, a)
            return <line key={`fe${f.id}`} x1={0} y1={0} x2={p.x} y2={p.y} className="map-edge map-edge-friend" />
          })}

          {/* people */}
          {people.map((p) => {
            const on = !dimming || lit(p)
            const s = score[p.id]
            const r = goal && s !== undefined ? 8 + (s / 100) * 11 : p.ring === 'close' ? 15 : p.ring === 'known' ? 10 : 8
            return (
              <g
                key={p.id}
                className={`map-node map-node-${p.ring} ${on ? '' : 'dim'} ${goal && s !== undefined ? 'hot' : ''}`}
                onClick={() => onOpen(p.id)}
                onMouseEnter={() => setHover(p.id)}
                onMouseLeave={() => setHover(null)}
                tabIndex={0}
                role="button"
                aria-label={`${p.name}${p.headline ? `, ${p.headline}` : ''}`}
                onKeyDown={(e) => e.key === 'Enter' && onOpen(p.id)}
              >
                <circle cx={p.x} cy={p.y} r={r} />
                {p.notes > 0 && <circle cx={p.x + r * 0.75} cy={p.y - r * 0.75} r={3.2} className="map-note-dot" />}
                {(showAllLabels || p.ring === 'close' || (dimming && on)) && (
                  <text {...labelProps(p, r + 5)} className="map-label">
                    {p.name.split(' ')[0]}
                  </text>
                )}
              </g>
            )
          })}

          {friends.map((f, i) => {
            const a = (i / friends.length) * TAU + Math.PI / 4
            const p = polar(rFriends, a)
            return (
              <g key={f.id} className="map-friend">
                <rect x={p.x - 18} y={p.y - 18} width={36} height={36} rx={10} />
                <text x={p.x} y={p.y + 1} textAnchor="middle" dominantBaseline="middle" className="map-friend-initial">
                  {f.name[0]}
                </text>
                <text x={p.x} y={p.y + 36} textAnchor="middle" className="map-label">
                  {f.name.split(' ')[0]}'s network
                </text>
              </g>
            )
          })}

          {/* you */}
          <circle r={28} className="map-me" />
          <text textAnchor="middle" dominantBaseline="middle" className="map-me-label">
            You
          </text>

          {hovered && (
            <g className="map-tip" pointerEvents="none">
              <rect x={hovered.x + 12} y={hovered.y - 34} width={Math.max(hovered.name.length, (hovered.headline || '').slice(0, 40).length) * 8.4 + 24} height={50} rx={8} />
              <text x={hovered.x + 22} y={hovered.y - 17} className="map-tip-name">
                {hovered.name}
              </text>
              <text x={hovered.x + 22} y={hovered.y + 4} className="map-tip-sub">
                {(hovered.headline || (hovered.ring === 'via' ? `Through ${lay.placed[hovered.via ?? '']?.name ?? 'someone'}` : '')).slice(0, 40)}
              </text>
            </g>
          )}
        </svg>
        <div className="map-zoom">
          <button className="btn btn-quiet btn-small" onClick={() => setZoom((z) => Math.max(1, z - 0.5))} disabled={zoom <= 1} aria-label="Zoom out">
            −
          </button>
          <button className="btn btn-quiet btn-small" onClick={() => setZoom((z) => Math.min(3, z + 0.5))} disabled={zoom >= 3} aria-label="Zoom in">
            +
          </button>
        </div>
      </div>

      <div className="legend map-legend">
        <span>
          <i className="key key-fit" /> Fits this goal
        </span>
        <span>
          <i className="key key-close" /> Inner circle
        </span>
        <span>
          <i className="key key-known" /> You know them
        </span>
        <span>
          <i className="key key-via" /> Heard of through someone
        </span>
        {friends.length > 0 && (
          <span>
            <i className="key key-friend" /> Friend: search their contacts in Find
          </span>
        )}
        <span>
          <i className="key key-note" /> Has your notes
        </span>
      </div>
      <p className="tiny faint">You are in the middle. Green dots fit this goal, bigger means a better fit. Grey dots are who you'd go through. Tap anyone to open their card and add a note.</p>
    </div>
  )
}
