import { useState } from 'react'
import { addAccount, api, listAccounts, switchAccount, type Account } from '../api'
import { Avatar, Wordmark } from '../components/Ring'

export function SignIn({ onDone, onCancel }: { onDone: () => void; onCancel?: () => void }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const accounts = listAccounts()

  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      const r = await api<Account>('/api/session', { body: { name }, auth: false })
      addAccount(r)
      onDone()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="signin">
      <Wordmark size={26} />
      <div className="orbit" aria-hidden>
        <span style={{ inset: 0, border: '2px dashed var(--line)' }} />
        <span style={{ inset: 30, border: '2px solid var(--ink)' }} />
        <span style={{ inset: 62, background: 'var(--close)' }} />
        <span style={{ width: 22, height: 22, top: 22, right: 30, background: 'var(--keep)' }} />
        <span style={{ width: 14, height: 14, bottom: 8, left: 44, background: 'var(--ink)' }} />
      </div>
      <div className="stack">
        <h1 className="signin-title">The people you know, sorted by what you're working toward.</h1>
        <p className="muted">
          Bring in your LinkedIn connections, swipe to keep the ones that matter, and ask friends' agents for intros without
          anyone's contacts changing hands.
        </p>
      </div>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          create()
        }}
      >
        <div className="field">
          <label htmlFor="name">Your name</label>
          <input id="name" className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" autoFocus />
        </div>
        {error && <p className="error">{error}</p>}
        <button className="btn btn-primary" disabled={busy || !name.trim()}>
          Create my graph
        </button>
        {onCancel && (
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            Cancel
          </button>
        )}
      </form>
      {accounts.length > 0 && (
        <div className="stack">
          <div className="section-title">On this device</div>
          {accounts.map((a) => (
            <button
              key={a.user.id}
              className="person-row"
              onClick={() => {
                switchAccount(a.user.id)
                onDone()
              }}
            >
              <Avatar name={a.user.name} size={36} />
              <span className="grow">
                <span className="person-name">{a.user.name}</span>
                <span className="small muted" style={{ display: 'block' }}>
                  {a.user.handle}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
    </main>
  )
}
