import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type Simulation, type SimulationNodeDatum } from 'd3-force'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Spinner } from './ui'

/* A free-floating graph of the people who fit a goal, in the style of Obsidian's graph view:
   physics layout, drag any node, scroll or pinch to zoom, drag the background to pan,
   hover to light up the path back to you. Everything comes from FalkorDB:
   your RELEVANT_TO edges, KNOWS ties, and friends' agent results. */

type Person = {
  id: string
  name: string
  headline: string
  company: string
  position: string
  strength: string | null
  direct: boolean
  notes: number
}
type Snapshot = { people: Person[]; ties: { a: string; b: string; relation: string | null }[]; relevant: { id: string; goal_id: string; score: number }[] }
type Friend = { name: string; handle: string }
export type FriendHit = {
  key: string
  name: string
  position: string
  company: string
  headline: string
  url: string
  degree: number
  score: number
  reason: string
  paths: { name: string; kind: string; handle?: string }[][]
}
export type AgentRun = { friends: Friend[]; people: FriendHit[] }

type Kind = 'me' | 'close' | 'known' | 'via' | 'friend' | 'hit'
type GNode = SimulationNodeDatum & {
  id: string
  kind: Kind
  name: string
  sub: string
  r: number
  fit: boolean
  notes?: number
  personId?: string
  url?: string
}
type GLink = { source: string | GNode; target: string | GNode; kind: 'direct' | 'close' | 'tie' | 'friend' | 'hit' }

const end = (v: string | GNode) => (typeof v === 'string' ? v : v.id)

function build(snap: Snapshot, agents: AgentRun | undefined, goalId: string) {
  const score: Record<string, number> = {}
  snap.relevant.filter((r) => r.goal_id === goalId).forEach((r) => (score[r.id] = r.score))
  const nodes: GNode[] = [{ id: 'me', kind: 'me', name: 'You', sub: '', r: 16, fit: false, fx: 0, fy: 0 }]
  const links: GLink[] = []
  const ids = new Set(snap.people.map((p) => p.id))

  snap.people.forEach((p) => {
    const s = score[p.id]
    const kind: Kind = p.direct ? (p.strength === 'close' ? 'close' : 'known') : 'via'
    nodes.push({
      id: p.id,
      kind,
      name: p.name,
      sub: p.headline || [p.position, p.company].filter(Boolean).join(' at '),
      r: s !== undefined ? 6 + (s / 100) * 9 : 5,
      fit: s !== undefined,
      notes: p.notes,
      personId: p.id,
    })
    if (p.direct) links.push({ source: 'me', target: p.id, kind: p.strength === 'close' ? 'close' : 'direct' })
  })
  // People you heard about hang off the contact you heard it from.
  snap.ties.forEach((t) => {
    if (ids.has(t.a) && ids.has(t.b)) links.push({ source: t.a, target: t.b, kind: 'tie' })
  })
  snap.people
    .filter((p) => !p.direct && !snap.ties.some((t) => t.a === p.id || t.b === p.id))
    .forEach((p) => links.push({ source: 'me', target: p.id, kind: 'tie' }))

  ;(agents?.friends ?? []).forEach((f) => {
    const fid = `f:${f.handle}`
    nodes.push({ id: fid, kind: 'friend', name: f.name, sub: 'Friend', r: 11, fit: false })
    links.push({ source: 'me', target: fid, kind: 'friend' })
  })
  ;(agents?.people ?? []).forEach((h) => {
    const hop = h.paths[0]?.[0]
    const friend = (agents?.friends ?? []).find((f) => f.handle === hop?.handle || f.name === hop?.name)
    if (!friend) return
    const hid = `h:${h.key}`
    nodes.push({
      id: hid,
      kind: 'hit',
      name: h.name,
      sub: `Through ${friend.name.split(' ')[0]}: ${[h.position, h.company].filter(Boolean).join(' at ') || h.headline}`,
      r: 5 + (h.score / 100) * 8,
      fit: true,
      url: h.url,
    })
    links.push({ source: `f:${friend.handle}`, target: hid, kind: 'hit' })
  })
  return { nodes, links }
}

/** Parent of each node on its shortest path back to you, for path highlighting. */
function parents(links: GLink[]) {
  const adj: Record<string, string[]> = {}
  links.forEach((l) => {
    const a = end(l.source)
    const b = end(l.target)
    ;(adj[a] ??= []).push(b)
    ;(adj[b] ??= []).push(a)
  })
  const parent: Record<string, string | null> = { me: null }
  const queue = ['me']
  while (queue.length) {
    const n = queue.shift()!
    for (const m of adj[n] ?? []) {
      if (!(m in parent)) {
        parent[m] = n
        queue.push(m)
      }
    }
  }
  return { parent, adj }
}

const DIST: Record<GLink['kind'], number> = { close: 70, direct: 115, tie: 55, friend: 150, hit: 60 }

export function NetworkMap({
  goalId,
  onOpen,
  agents,
}: {
  goalId: string
  onOpen: (id: string) => void
  agents?: AgentRun
  onAgents?: (r: AgentRun) => void
}) {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [q, setQ] = useState('')
  const [hover, setHover] = useState<string | null>(null)
  const [, setFrame] = useState(0)
  const [view, setView] = useState({ x: 0, y: 0, k: 1 })
  const [size, setSize] = useState({ w: 600, h: 520 })
  const box = useRef<HTMLDivElement>(null)
  const sim = useRef<Simulation<GNode, GLink> | null>(null)
  const graph = useRef<{ nodes: GNode[]; links: GLink[]; from?: Snapshot }>({ nodes: [], links: [] })
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const gesture = useRef<{ mode: 'pan' | 'node' | 'pinch' | null; node?: GNode; start?: { x: number; y: number }; moved?: boolean; dist?: number; view?: typeof view }>({ mode: null })

  useEffect(() => {
    api<Snapshot>(`/api/graph?goal=${encodeURIComponent(goalId)}`).then(setSnap)
  }, [goalId])

  // The graph box only exists once the layout is built, so DOM listeners wait for it.
  const built = !!snap && graph.current.from === snap

  // Track the container size so the graph fills it.
  useEffect(() => {
    const el = box.current
    if (!el) return
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    setSize({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [built])

  const fit = useCallback(
    (nodes: GNode[] = graph.current.nodes) => {
      if (!nodes.length) return
      const xs = nodes.map((n) => n.x ?? 0)
      const ys = nodes.map((n) => n.y ?? 0)
      const minX = Math.min(...xs) - 40
      const maxX = Math.max(...xs) + 40
      const minY = Math.min(...ys) - 40
      const maxY = Math.max(...ys) + 40
      const k = Math.min(2, Math.max(0.3, Math.min(size.w / (maxX - minX), size.h / (maxY - minY))))
      setView({ k, x: size.w / 2 - ((minX + maxX) / 2) * k, y: size.h / 2 - ((minY + maxY) / 2) * k })
    },
    [size.w, size.h],
  )

  // Build the graph and run the physics.
  useEffect(() => {
    if (!snap) return
    const g = build(snap, agents, goalId)
    // Keep positions of nodes that already exist, so new friends' people float in gently.
    const old = new Map(graph.current.nodes.map((n) => [n.id, n]))
    g.nodes.forEach((n) => {
      const o = old.get(n.id)
      if (o) {
        n.x = o.x
        n.y = o.y
      }
    })
    graph.current = { ...g, from: snap }
    setFrame((f) => f + 1)
    sim.current?.stop()
    const s = forceSimulation<GNode>(g.nodes)
      .force('link', forceLink<GNode, GLink>(g.links).id((n) => n.id).distance((l) => DIST[l.kind]).strength(0.6))
      .force('charge', forceManyBody<GNode>().strength((n) => (n.kind === 'me' ? -500 : n.kind === 'friend' ? -300 : -140)))
      .force('collide', forceCollide<GNode>().radius((n) => n.r + 8))
      .force('x', forceX<GNode>(0).strength(0.03))
      .force('y', forceY<GNode>(0).strength(0.03))
    if (!old.size) {
      s.stop()
      s.tick(160) // settle before the first paint, then keep it gently alive
      fit(g.nodes)
      s.alpha(0.05).restart()
    } else {
      s.alpha(0.5).alphaDecay(0.05) // new people float in and settle within a second or two
    }
    let raf = 0
    s.on('tick', () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => setFrame((f) => f + 1))
    })
    sim.current = s
    return () => {
      s.stop()
      cancelAnimationFrame(raf)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snap, agents, goalId])


  // Frame everyone once the box has its real size (and again if it is resized).
  useEffect(() => {
    if (built) fit()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [built, size.w, size.h])

  const toGraph = (clientX: number, clientY: number) => {
    const rect = box.current!.getBoundingClientRect()
    return { x: (clientX - rect.left - view.x) / view.k, y: (clientY - rect.top - view.y) / view.k }
  }

  const zoomAt = (mx: number, my: number, factor: number) => {
    setView((v) => {
      const k = Math.min(4, Math.max(0.25, v.k * factor))
      return { k, x: mx - (mx - v.x) * (k / v.k), y: my - (my - v.y) * (k / v.k) }
    })
  }

  // Wheel zoom has to be a non-passive listener so the page does not scroll.
  useEffect(() => {
    const el = box.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      zoomAt(e.clientX - rect.left, e.clientY - rect.top, Math.exp(-e.deltaY * 0.0015))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [built])

  const onPointerDown = (e: React.PointerEvent, node?: GNode) => {
    e.stopPropagation()
    ;(e.currentTarget as Element).setPointerCapture?.(e.pointerId)
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()]
      gesture.current = { mode: 'pinch', dist: Math.hypot(a.x - b.x, a.y - b.y), view }
      return
    }
    if (node) {
      node.fx = node.x
      node.fy = node.y
      sim.current?.alphaTarget(0.25).restart()
      gesture.current = { mode: 'node', node, start: { x: e.clientX, y: e.clientY }, moved: false }
    } else {
      gesture.current = { mode: 'pan', start: { x: e.clientX - view.x, y: e.clientY - view.y }, moved: false }
    }
  }

  const onPointerMove = (e: React.PointerEvent) => {
    if (!pointers.current.has(e.pointerId)) return
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    const gs = gesture.current
    if (gs.mode === 'pinch' && pointers.current.size === 2 && gs.dist && gs.view) {
      const [a, b] = [...pointers.current.values()]
      const rect = box.current!.getBoundingClientRect()
      const mx = (a.x + b.x) / 2 - rect.left
      const my = (a.y + b.y) / 2 - rect.top
      const k = Math.min(4, Math.max(0.25, gs.view.k * (Math.hypot(a.x - b.x, a.y - b.y) / gs.dist)))
      setView({ k, x: mx - (mx - gs.view.x) * (k / gs.view.k), y: my - (my - gs.view.y) * (k / gs.view.k) })
    } else if (gs.mode === 'node' && gs.node && gs.start) {
      if (Math.hypot(e.clientX - gs.start.x, e.clientY - gs.start.y) > 4) gs.moved = true
      const p = toGraph(e.clientX, e.clientY)
      gs.node.fx = p.x
      gs.node.fy = p.y
    } else if (gs.mode === 'pan' && gs.start) {
      gs.moved = true
      setView((v) => ({ ...v, x: e.clientX - gs.start!.x, y: e.clientY - gs.start!.y }))
    }
  }

  const onPointerUp = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId)
    const gs = gesture.current
    if (gs.mode === 'node' && gs.node) {
      const n = gs.node
      if (n.kind !== 'me') {
        n.fx = null
        n.fy = null
      }
      sim.current?.alphaTarget(0)
      if (!gs.moved) {
        if (n.personId) onOpen(n.personId)
        else if (n.url) window.open(n.url, '_blank', 'noopener')
      }
    }
    if (pointers.current.size === 0) gesture.current = { mode: null }
  }

  if (!built) return <Spinner />
  const { nodes, links } = graph.current
  const { parent, adj } = parents(links)
  if (nodes.length <= 1) return <p className="muted">No one fits this goal yet, so there is nothing to map.</p>

  // What to light up: the hovered node, its neighbours, and its path back to you.
  const lit = new Set<string>()
  const litEdges = new Set<string>()
  if (hover) {
    lit.add(hover)
    ;(adj[hover] ?? []).forEach((m) => {
      lit.add(m)
      litEdges.add([hover, m].sort().join('|'))
    })
    let n: string | null | undefined = hover
    while (n && parent[n] !== undefined && parent[n] !== null) {
      const p: string = parent[n]!
      lit.add(p)
      litEdges.add([n, p].sort().join('|'))
      n = p
    }
  }
  const term = q.trim().toLowerCase()
  const match = (n: GNode) => !term || `${n.name} ${n.sub}`.toLowerCase().includes(term)
  const focus = Boolean(hover || term)
  const on = (n: GNode) => (hover ? lit.has(n.id) : match(n))
  const hovered = hover ? nodes.find((n) => n.id === hover) : undefined
  const showLabel = (n: GNode) => n.kind === 'me' || n.kind === 'friend' || lit.has(n.id) || (term && match(n)) || view.k >= 1.2 || nodes.length <= 30

  return (
    <div className="stack map-wrap">
      <div className="map-controls">
        <input className="input" type="search" placeholder="Highlight by name, role or company" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Highlight people" />
      </div>

      <div
        className="fg"
        ref={box}
        onPointerDown={(e) => onPointerDown(e)}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <svg width={size.w} height={size.h} role="img" aria-label={`Graph of ${nodes.length - 1} people around you`}>
          <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
            {links.map((l) => {
              const a = l.source as GNode
              const b = l.target as GNode
              const key = [a.id, b.id].sort().join('|')
              const hot = litEdges.has(key)
              const dim = focus && !hot && !(term && match(a) && match(b))
              return <line key={key} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={`fg-edge fg-edge-${l.kind} ${hot ? 'hot' : ''} ${dim ? 'dim' : ''}`} />
            })}
            {nodes.map((n) => {
              const dim = focus && !on(n)
              return (
                <g
                  key={n.id}
                  className={`fg-node fg-${n.kind} ${n.fit ? 'fit' : ''} ${dim ? 'dim' : ''} ${hover === n.id ? 'hover' : ''}`}
                  transform={`translate(${n.x ?? 0},${n.y ?? 0})`}
                  onPointerDown={(e) => onPointerDown(e, n)}
                  onPointerEnter={() => setHover(n.id)}
                  onPointerLeave={() => setHover((h) => (h === n.id ? null : h))}
                  tabIndex={n.kind === 'me' ? -1 : 0}
                  role={n.kind === 'me' ? undefined : 'button'}
                  aria-label={n.kind === 'me' ? undefined : `${n.name}${n.sub ? `, ${n.sub}` : ''}`}
                  onFocus={() => setHover(n.id)}
                  onBlur={() => setHover(null)}
                  onKeyDown={(e) => {
                    if (e.key !== 'Enter') return
                    if (n.personId) onOpen(n.personId)
                    else if (n.url) window.open(n.url, '_blank', 'noopener')
                  }}
                >
                  <circle r={n.r} />
                  {n.kind === 'me' && (
                    <text className="fg-me-label" textAnchor="middle" dominantBaseline="central">
                      You
                    </text>
                  )}
                  {n.kind === 'friend' && (
                    <text className="fg-friend-initial" textAnchor="middle" dominantBaseline="central">
                      {n.name[0]}
                    </text>
                  )}
                  {n.notes ? <circle className="fg-note" r={2.6} cx={n.r * 0.75} cy={-n.r * 0.75} /> : null}
                  {n.kind !== 'me' && showLabel(n) && (
                    <text className="fg-label" y={n.r + 12} textAnchor="middle">
                      {n.kind === 'friend' ? n.name.split(' ')[0] : n.name.split(' ')[0]}
                    </text>
                  )}
                </g>
              )
            })}
          </g>
        </svg>

        {hovered && hovered.kind !== 'me' && (
          <div className="fg-tip" style={{ left: Math.min(size.w - 240, Math.max(8, (hovered.x ?? 0) * view.k + view.x + 14)), top: Math.max(8, (hovered.y ?? 0) * view.k + view.y - 52) }}>
            <strong>{hovered.name}</strong>
            {hovered.sub && <span>{hovered.sub}</span>}
            <em>{hovered.personId ? 'Click to open their card' : hovered.url ? 'Click for LinkedIn' : ''}</em>
          </div>
        )}

        <div className="fg-tools" onPointerDown={(e) => e.stopPropagation()}>
          <button className="btn btn-quiet btn-small" onClick={() => zoomAt(size.w / 2, size.h / 2, 1 / 1.3)} aria-label="Zoom out">
            −
          </button>
          <button className="btn btn-quiet btn-small" onClick={() => zoomAt(size.w / 2, size.h / 2, 1.3)} aria-label="Zoom in">
            +
          </button>
          <button className="btn btn-quiet btn-small" onClick={() => fit()}>
            Fit
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
          <i className="key key-via" /> Heard of through someone
        </span>
        {(agents?.friends?.length ?? 0) > 0 && (
          <span>
            <i className="key key-friend" /> Friend
          </span>
        )}
        {(agents?.people?.length ?? 0) > 0 && (
          <span>
            <i className="key key-hit" /> Fits, through a friend
          </span>
        )}
      </div>
      <p className="tiny faint">
        Hover anyone to light up their path to you. Drag people around, scroll or pinch to zoom, drag the background to move. After you tap Ask your network,
        your friends and the people their agents found join the graph.
      </p>
    </div>
  )
}
