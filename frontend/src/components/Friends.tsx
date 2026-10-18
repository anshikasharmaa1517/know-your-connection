import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Avatar } from './Ring'
import { useToast } from './ui'

type Friend = { id: string; name: string; handle: string }

/** Friends live in the account menu: their agents are what "Ask your network" runs. */
export function Friends({ handle: myHandle, onChange }: { handle: string; onChange?: (incoming: number) => void }) {
  const [data, setData] = useState<{ friends: Friend[]; incoming: Friend[]; outgoing: Friend[] } | null>(null)
  const [handle, setHandle] = useState('')
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  const load = useCallback(async () => {
    const f = await api('/api/friends')
    setData(f)
    onChange?.(f.incoming.length)
  }, [onChange])

  useEffect(() => {
    load().catch((e) => toast(e.message))
  }, [load, toast])

  const run = async (fn: () => Promise<any>, ok: string) => {
    setBusy(true)
    try {
      await fn()
      toast(ok)
      await load()
    } catch (e: any) {
      toast(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="spread">
        <div className="section-title">Friends</div>
        <span className="small muted">
          Your handle: <span className="handle">{myHandle}</span>
        </span>
      </div>
      <p className="tiny faint">When a goal has a gap, your friends' agents search their networks for it straight away.</p>
      {data?.incoming.map((f) => (
        <div className="spread" key={f.id}>
          <span className="row">
            <Avatar name={f.name} size={32} /> {f.name} wants to connect
          </span>
          <button className="btn btn-primary btn-small" disabled={busy} onClick={() => run(() => api(`/api/friends/${f.id}/accept`, { method: 'POST' }), `You and ${f.name} are friends`)}>
            Accept
          </button>
        </div>
      ))}
      {data && data.friends.length > 0 && (
        <div className="row row-wrap" style={{ gap: 8 }}>
          {data.friends.map((f) => (
            <span className="chip" key={f.id}>
              {f.name}
            </span>
          ))}
        </div>
      )}
      {data?.outgoing.map((f) => (
        <span className="small muted" key={f.id}>
          Waiting for {f.name} to accept
        </span>
      ))}
      <form
        className="find-form"
        onSubmit={(e) => {
          e.preventDefault()
          run(async () => {
            await api('/api/friends', { body: { handle } })
            setHandle('')
          }, 'Friend request sent')
        }}
      >
        <input className="input" placeholder="Friend's handle" value={handle} onChange={(e) => setHandle(e.target.value)} aria-label="Friend's handle" />
        <button className="btn btn-quiet" disabled={busy || !handle.trim()}>
          Add
        </button>
      </form>
    </div>
  )
}

/** Anywhere in the app can ask the shell to open the account menu at Friends. */
export function openFriends() {
  window.dispatchEvent(new Event('connections:friends'))
}
