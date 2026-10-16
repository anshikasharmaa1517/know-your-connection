import { Fragment, useCallback, useEffect, useState } from 'react'
import { api, copy } from '../api'
import { Avatar } from '../components/Ring'
import { Spinner, useToast } from '../components/ui'
import type { AskDraft, Tab } from '../App'

type Friend = { id: string; name: string; handle: string }

export function AskView({ draft, clearDraft, me, go, onInbox }: {
  draft: AskDraft | null
  clearDraft: () => void
  me: Friend
  go: (t: Tab) => void
  onInbox: (n: number) => void
}) {
  const [friends, setFriends] = useState<{ friends: Friend[]; incoming: Friend[]; outgoing: Friend[] } | null>(null)
  const [outbox, setOutbox] = useState<any[]>([])
  const [inbox, setInbox] = useState<any[]>([])
  const [handle, setHandle] = useState('')
  const [need, setNeed] = useState(draft?.need ?? '')
  const [desc, setDesc] = useState(draft?.description ?? '')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  const load = useCallback(async () => {
    const [f, o, i] = await Promise.all([api('/api/friends'), api('/api/exchange/outbox'), api('/api/exchange/inbox')])
    setFriends(f)
    setOutbox(o.requests)
    setInbox(i.requests)
    onInbox(i.requests.filter((r: any) => r.reply?.status === 'intro_requested').length + f.incoming.length)
  }, [onInbox])

  useEffect(() => {
    load().catch((e) => toast(e.message))
    const t = window.setInterval(() => load().catch(() => {}), 6000)
    return () => window.clearInterval(t)
  }, [load, toast])

  useEffect(() => {
    if (draft) {
      setNeed(draft.need)
      setDesc(draft.description)
    }
  }, [draft])

  const run = async (fn: () => Promise<any>, ok?: string) => {
    setBusy(true)
    try {
      const r = await fn()
      if (ok) toast(ok)
      await load()
      return r
    } catch (e: any) {
      toast(e.message)
    } finally {
      setBusy(false)
    }
  }

  const post = () =>
    run(async () => {
      await api('/api/exchange/requests', {
        body: { need, description: desc, note, gap_id: draft?.gap_id, goal_id: draft?.goal_id },
      })
      setNeed('')
      setDesc('')
      setNote('')
      clearDraft()
    }, "Sent. Your friends' agents are checking their own networks.")

  const friendsSection = (
    <>
      <section className="panel stack">
        <div className="row spread">
          <div className="section-title">Friends</div>
          <span className="small muted">
            Your handle: <span className="handle">{me.handle}</span>
          </span>
        </div>
        {friends?.incoming.map((f) => (
          <div className="row spread" key={f.id}>
            <span className="row">
              <Avatar name={f.name} size={32} /> {f.name} wants to connect
            </span>
            <button className="btn btn-primary btn-small" disabled={busy} onClick={() => run(() => api(`/api/friends/${f.id}/accept`, { method: 'POST' }), `You and ${f.name} are friends`)}>
              Accept
            </button>
          </div>
        ))}
        {friends && friends.friends.length > 0 && (
          <div className="row row-wrap" style={{ gap: 8 }}>
            {friends.friends.map((f) => (
              <span className="chip" key={f.id}>
                {f.name}
              </span>
            ))}
          </div>
        )}
        {friends?.outgoing.map((f) => (
          <span className="small muted" key={f.id}>
            Waiting for {f.name} to accept
          </span>
        ))}
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault()
            run(async () => {
              const r = await api('/api/friends', { body: { handle } })
              setHandle('')
              return r
            }, 'Friend request sent')
          }}
        >
          <input className="input" placeholder="Friend's handle" value={handle} onChange={(e) => setHandle(e.target.value)} aria-label="Friend's handle" />
          <button className="btn btn-quiet" disabled={busy || !handle.trim()}>
            Add
          </button>
        </form>
      </section>

    </>
  )
  const formSection = (
    <>
      <section className="panel stack">
        <div className="section-title">{draft ? `New request for "${draft.goal}"` : 'New request'}</div>
        <div className="field">
          <label htmlFor="need">Who are you looking for?</label>
          <input id="need" className="input" value={need} onChange={(e) => setNeed(e.target.value)} placeholder="climate-tech VC" />
        </div>
        <div className="field">
          <label htmlFor="desc">What would help</label>
          <input id="desc" className="input" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="An investor who'd take a 20 minute call about internships" />
        </div>
        <div className="field">
          <label htmlFor="note">Note to friends (optional)</label>
          <input id="note" className="input" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <p className="tiny faint">Friends see your name and these words. Nothing from your network is shared.</p>
        <button className="btn btn-primary" disabled={busy || !need.trim() || !friends?.friends.length} onClick={post}>
          Send to {friends?.friends.length ?? 0} {friends?.friends.length === 1 ? 'friend' : 'friends'}
        </button>
        {friends && friends.friends.length === 0 && <p className="small muted">Add a friend first. Their agent is who you ask.</p>}
      </section>

    </>
  )
  const inboxSection = (
    <>
      {inbox.length > 0 && (
        <section className="stack">
          <h2>Asked of you</h2>
          {inbox.map((r) => (
            <InboxItem key={r.request.id} item={r} busy={busy} run={run} />
          ))}
        </section>
      )}

    </>
  )
  const outboxSection = (
    <>
      {outbox.length > 0 && (
        <section className="stack">
          <h2>Your requests</h2>
          {outbox.map((r) => (
            <div className="panel stack" key={r.request.id}>
              <div>
                <h3>{r.request.need}</h3>
                {r.request.description && <p className="small muted">{r.request.description}</p>}
              </div>
              {r.replies.length === 0 && (
                <p className="small muted row">
                  <Spinner /> Waiting on {r.asked} {r.asked === 1 ? 'agent' : 'agents'}
                </p>
              )}
              {r.replies.map((rep: any) => (
                <OutboxReply key={rep.reply.id} rep={rep} busy={busy} run={run} go={go} />
              ))}
            </div>
          ))}
        </section>
      )}
    </>
  )
  const noFriends = !!friends && friends.friends.length === 0 && friends.incoming.length === 0
  const order = draft
    ? [formSection, inboxSection, outboxSection, friendsSection]
    : noFriends
      ? [friendsSection, formSection, inboxSection, outboxSection]
      : [inboxSection, outboxSection, formSection, friendsSection]

  return (
    <div className="view">
      <div className="view-head">
        <h1>Ask your network</h1>
        <p className="muted">
          Your request goes to your friends' agents. Each one searches only its owner's network and answers with a count, never
          a name. Names only appear when the person they match writes their own card.
        </p>
      </div>

      {order.map((section, i) => (
        <Fragment key={i}>{section}</Fragment>
      ))}
    </div>
  )
}

function OutboxReply({ rep, busy, run, go }: { rep: any; busy: boolean; run: (fn: () => Promise<any>, ok?: string) => Promise<any>; go: (t: Tab) => void }) {
  const r = rep.reply
  const card = rep.card
  const first = r.from_name.split(' ')[0]
  const toast = useToast()
  let line = ''
  if (r.status === 'no_match') line = `${first}'s agent found no one who fits.`
  else line = `${first}'s agent found ${r.count} ${r.count === 1 ? 'person' : 'people'} who fit${r.strength === 'close' ? ', and one is in their inner circle' : ''}.`

  return (
    <div className="reply">
      <div className="row">
        <Avatar name={r.from_name} size={32} tie={r.strength === 'close' ? 'close' : 'known'} />
        <span>{line}</span>
      </div>
      {r.status === 'answered' && (
        <button className="btn btn-quiet btn-small" disabled={busy} onClick={() => run(() => api(`/api/exchange/replies/${r.id}/ask`, { method: 'POST' }), `Asked ${first} for an intro`)}>
          Ask {first} for an intro
        </button>
      )}
      {r.status === 'intro_requested' && <p className="small muted">Waiting for {first} to pick someone and ask them.</p>}
      {r.status === 'declined' && <p className="small muted">{first} passed on this one.</p>}
      {r.status === 'approved' && card?.status === 'awaiting_optin' && (
        <p className="small muted">{first} asked their contact. You'll see a card if they say yes.</p>
      )}
      {card?.status === 'declined' && <p className="small muted">Their contact said no thanks.</p>}
      {card?.status === 'shared' && (
        <div className="card-reveal">
          <div className="row">
            <Avatar name={card.name} size={40} />
            <div>
              <strong>{card.name}</strong>
              {card.line && <div className="small">{card.line}</div>}
            </div>
          </div>
          <div className="small">
            Reach them at <strong>{card.contact}</strong>
          </div>
          <div className="section-title" style={{ marginTop: 6 }}>
            Intro draft
          </div>
          <div className="draft">{card.intro_draft}</div>
          <div className="row row-wrap">
            <button className="btn btn-quiet btn-small" onClick={async () => toast((await copy(card.intro_draft)) ? 'Copied' : 'Copy failed')}>
              Copy draft
            </button>
            {card.saved ? (
              <button className="btn btn-ghost btn-small" onClick={() => go('people')}>
                In your people
              </button>
            ) : (
              <button className="btn btn-primary btn-small" disabled={busy} onClick={() => run(() => api(`/api/exchange/cards/${card.id}/save`, { method: 'POST' }), `${card.name} added to your people`)}>
                Add to my people
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function InboxItem({ item, busy, run }: { item: any; busy: boolean; run: (fn: () => Promise<any>, ok?: string) => Promise<any> }) {
  const [pick, setPick] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [approved, setApproved] = useState<any>(null)
  const toast = useToast()
  const rep = item.reply
  const from = item.request.from_name.split(' ')[0]

  return (
    <div className="panel stack">
      <div>
        <h3>
          {item.request.from_name} is looking for {item.request.need}
        </h3>
        {item.request.description && <p className="small muted">{item.request.description}</p>}
        {item.request.note && <p className="small">"{item.request.note}"</p>}
      </div>
      <p className="small">
        {rep?.status === 'no_match'
          ? `Your agent found no one in your network who fits. ${from} sees that you have no match.`
          : `Your agent told ${from} you know ${rep?.count ?? 0} who fit. Only you can see who.`}
      </p>
      {item.matches.length > 0 && (
        <div>
          {item.matches.map((m: any) => (
            <label className="match" key={m.person.id}>
              {rep?.status === 'intro_requested' && (
                <input type="radio" name={`pick-${item.request.id}`} checked={pick === m.person.id} onChange={() => setPick(m.person.id)} />
              )}
              <Avatar name={m.person.name} size={36} />
              <span>
                <strong>{m.person.name}</strong>
                {m.chosen && <span className="chip chip-keep" style={{ marginLeft: 6 }}>asked</span>}
                <span className="small muted" style={{ display: 'block' }}>
                  {m.reason}
                </span>
              </span>
            </label>
          ))}
        </div>
      )}
      {rep?.status === 'answered' && <p className="small muted">{from} hasn't asked for an intro yet.</p>}
      {rep?.status === 'intro_requested' && !approved && (
        <>
          <p className="small">
            <strong>{from} asked for an intro.</strong> Pick one person to ask. They'll get a link and decide for themselves.
          </p>
          <input className="input" placeholder={`A line about ${from} for them (optional)`} value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="row">
            <button
              className="btn btn-primary btn-small"
              disabled={busy || !pick}
              onClick={async () => {
                const r = await run(() => api(`/api/exchange/replies/${rep.id}/approve`, { body: { person_id: pick, note } }))
                if (r) setApproved(r)
              }}
            >
              Ask them
            </button>
            <button className="btn btn-ghost btn-small" disabled={busy} onClick={() => run(() => api(`/api/exchange/replies/${rep.id}/decline`, { method: 'POST' }), 'Passed')}>
              Pass
            </button>
          </div>
        </>
      )}
      {approved && (
        <div className="card-reveal">
          <div className="section-title">Send this to them</div>
          <div className="draft">{approved.message.replace(approved.optin_link, `${window.location.origin}${approved.optin_path}`)}</div>
          <div className="row row-wrap">
            <button
              className="btn btn-primary btn-small"
              onClick={async () => toast((await copy(approved.message.replace(approved.optin_link, `${window.location.origin}${approved.optin_path}`))) ? 'Copied' : 'Copy failed')}
            >
              Copy message
            </button>
            {approved.contact_email && (
              <a className="btn btn-quiet btn-small" href={`mailto:${approved.contact_email}?subject=${encodeURIComponent('An intro?')}&body=${encodeURIComponent(approved.message.replace(approved.optin_link, `${window.location.origin}${approved.optin_path}`))}`}>
                Open in email
              </a>
            )}
            <a className="btn btn-ghost btn-small" href={approved.optin_path} target="_blank" rel="noreferrer">
              Preview their page
            </a>
          </div>
        </div>
      )}
      {rep?.status === 'approved' && !approved && <p className="small muted">You asked your contact. Waiting on them.</p>}
    </div>
  )
}
