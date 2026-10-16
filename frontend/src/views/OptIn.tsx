import { useEffect, useState } from 'react'
import { api } from '../api'
import { Wordmark } from '../components/Ring'
import { Spinner } from '../components/ui'

type View = { status: string; requester_name: string; broker_name: string; need: string; broker_note: string }

export function OptInPage({ token }: { token: string }) {
  const [view, setView] = useState<View | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [line, setLine] = useState('')
  const [contact, setContact] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<'shared' | 'declined' | null>(null)

  useEffect(() => {
    api<View>(`/api/optin/${token}`, { auth: false })
      .then(setView)
      .catch((e) => setError(e.message))
  }, [token])

  const send = async (accept: boolean) => {
    setBusy(true)
    setError(null)
    try {
      const r = await api(`/api/optin/${token}`, { body: { accept, name, line, contact }, auth: false })
      setDone(r.status)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const broker = view?.broker_name.split(' ')[0]
  const requester = view?.requester_name.split(' ')[0]

  return (
    <main className="signin">
      <Wordmark size={24} />
      {!view && !error && <Spinner />}
      {error && !view && <p className="error">{error}</p>}
      {view && (done || view.status !== 'awaiting_optin') ? (
        <div className="stack">
          <h1>{(done ?? view.status) === 'shared' ? 'Card shared' : 'Got it, no card shared'}</h1>
          <p className="muted">
            {(done ?? view.status) === 'shared'
              ? `${requester} can now see the card you wrote and will reach out. ${broker} will make the intro.`
              : `${requester} won't see anything about you. Thanks for answering ${broker}.`}
          </p>
        </div>
      ) : (
        view && (
          <form
            className="stack"
            style={{ gap: 18 }}
            onSubmit={(e) => {
              e.preventDefault()
              send(true)
            }}
          >
            <div className="stack" style={{ gap: 8 }}>
              <h1>
                {broker} would like to introduce you to {view.requester_name}
              </h1>
              <p className="muted">
                {requester} is looking for {view.need}. {requester} doesn't know who you are. If you'd like to talk, write a short card
                below. Only what you write here is shared.
              </p>
              {view.broker_note && <p className="notice">"{view.broker_note}" from {broker}</p>}
            </div>
            <div className="field">
              <label htmlFor="n">Your name</label>
              <input id="n" className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
            </div>
            <div className="field">
              <label htmlFor="l">One line about you</label>
              <input id="l" className="input" value={line} onChange={(e) => setLine(e.target.value)} placeholder="Climate investor, happy to talk internships" />
            </div>
            <div className="field">
              <label htmlFor="c">How to reach you</label>
              <input id="c" className="input" value={contact} onChange={(e) => setContact(e.target.value)} placeholder="Email or LinkedIn URL" />
            </div>
            {error && <p className="error">{error}</p>}
            <button className="btn btn-primary" disabled={busy || !name.trim() || !contact.trim()}>
              Share my card
            </button>
            <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => send(false)}>
              No thanks
            </button>
          </form>
        )
      )}
    </main>
  )
}
