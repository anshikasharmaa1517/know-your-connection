import { useCallback, useEffect, useState } from 'react'
import { api, copy } from '../api'
import { Avatar } from '../components/Ring'
import { Spinner, useToast } from '../components/ui'

type Friend = { id: string; name: string; handle: string; contacts?: number; discoverable?: boolean }
type Data = { friends: Friend[]; incoming: Friend[]; outgoing: Friend[] }

export function FriendsView({ handle: myHandle, onBadge }: { handle: string; onBadge: (n: number) => void }) {
  const [data, setData] = useState<Data | null>(null)
  const [handle, setHandle] = useState('')
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  const load = useCallback(async () => {
    const f = await api<Data>('/api/friends')
    setData(f)
    onBadge(f.incoming.length)
  }, [onBadge])

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

  const reach = data?.friends.reduce((n, f) => n + (f.contacts ?? 0), 0) ?? 0

  return (
    <div className="view">
      <div className="view-head">
        <h1>Friends</h1>
        <p className="muted">
          Your friends' agents search their networks when a goal has a gap, and Find shows who they know as 2nd degree.
        </p>
      </div>

      <section className="panel stack">
        <div className="section-title">Add a friend</div>
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
          <input className="input" placeholder="Their handle" value={handle} onChange={(e) => setHandle(e.target.value)} aria-label="Friend's handle" />
          <button className="btn btn-primary" disabled={busy || !handle.trim()}>
            Add
          </button>
        </form>
        <div className="spread">
          <span className="small muted">
            Your handle: <span className="handle">{myHandle}</span>
          </span>
          <button className="btn btn-ghost btn-small" onClick={async () => toast((await copy(myHandle)) ? 'Handle copied' : 'Copy failed')}>
            Copy
          </button>
        </div>
      </section>

      {!data ? (
        <Spinner />
      ) : (
        <>
          {data.incoming.length > 0 && (
            <section className="stack">
              <h2 className="friends-head">Waiting for you</h2>
              <div className="panel">
                {data.incoming.map((f) => (
                  <div className="person-row hit" key={f.id}>
                    <Avatar name={f.name} size={40} />
                    <span className="grow">
                      <span className="person-name">{f.name}</span>
                      <span className="small muted" style={{ display: 'block' }}>
                        {f.handle} wants to be friends
                      </span>
                    </span>
                    <button className="btn btn-primary btn-small" disabled={busy} onClick={() => run(() => api(`/api/friends/${f.id}/accept`, { method: 'POST' }), `You and ${f.name} are friends`)}>
                      Accept
                    </button>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="stack">
            <h2 className="friends-head">Your friends{data.friends.length > 0 ? ` (${data.friends.length})` : ''}</h2>
            {data.friends.length === 0 ? (
              <p className="small muted">No friends yet. Share your handle or add theirs above.</p>
            ) : (
              <>
                <p className="small muted">
                  Together their agents can search {reach} {reach === 1 ? 'contact' : 'contacts'} for you.
                </p>
                <div className="panel">
                  {data.friends.map((f) => (
                    <div className="person-row hit" key={f.id}>
                      <Avatar name={f.name} size={40} tie="close" />
                      <span className="grow">
                        <span className="person-name">{f.name}</span>
                        <span className="small muted" style={{ display: 'block' }}>
                          {f.handle}
                        </span>
                        <span className="tiny faint" style={{ display: 'block' }}>
                          {f.discoverable
                            ? `${f.contacts ?? 0} ${f.contacts === 1 ? 'contact' : 'contacts'} their agent can search`
                            : 'Has turned off search, so their agent stays quiet'}
                        </span>
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </section>

          {data.outgoing.length > 0 && (
            <section className="stack">
              <h2 className="friends-head">Sent</h2>
              <div className="panel">
                {data.outgoing.map((f) => (
                  <div className="person-row hit" key={f.id}>
                    <Avatar name={f.name} size={40} tie="via" />
                    <span className="grow">
                      <span className="person-name">{f.name}</span>
                      <span className="small muted" style={{ display: 'block' }}>
                        Waiting for them to accept
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  )
}
