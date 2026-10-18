import { useCallback, useEffect, useState } from 'react'
import { api, formatDate } from '../api'
import { Avatar, tieOf } from './Ring'
import { Sheet, Spinner, useToast } from './ui'

const KIND_TITLE: Record<string, string> = { intent: 'What they want', promise: 'Promises', fact: 'Notes' }

export function PersonSheet({ id, onClose }: { id: string; onClose: () => void }) {
  const [d, setD] = useState<any>(null)
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const toast = useToast()
  const load = useCallback(() => api(`/api/people/${id}`).then(setD).catch((e) => toast(e.message)), [id, toast])
  useEffect(() => {
    load()
  }, [load])

  const save = async () => {
    if (!note.trim()) return
    setSaving(true)
    try {
      await api(`/api/people/${id}/notes`, { method: 'POST', body: { text: note } })
      setNote('')
      await load()
    } catch (e: any) {
      toast(e.message)
    } finally {
      setSaving(false)
    }
  }

  const remove = async (fid: string) => {
    try {
      await api(`/api/people/${id}/notes/${fid}`, { method: 'DELETE' })
      await load()
    } catch (e: any) {
      toast(e.message)
    }
  }

  return (
    <Sheet onClose={onClose} label={d?.person.name ?? 'Person'}>
      {!d ? (
        <Spinner />
      ) : (
        <>
          <div className="row" style={{ alignItems: 'flex-start', gap: 14 }}>
            <Avatar name={d.person.name} tie={tieOf(d.strength, d.ties?.[0]?.person?.name)} size={60} />
            <div className="stack" style={{ gap: 4, flex: 1 }}>
              <h2>{d.person.name}</h2>
              {d.person.headline && <p className="muted">{d.person.headline}</p>}
              <div className="row row-wrap" style={{ gap: 6 }}>
                {d.strength === 'close' && <span className="chip chip-close">Inner circle</span>}
                {d.strength === 'known' && <span className="chip">You know them</span>}
                {!d.strength && <span className="chip">Not met yet</span>}
                {d.since && <span className="chip">Connected {formatDate(d.since)}</span>}
              </div>
            </div>
          </div>
          {(d.person.linkedin_url || d.person.email) && (
            <div className="row row-wrap small">
              {d.person.linkedin_url && (
                <a href={d.person.linkedin_url} target="_blank" rel="noreferrer">
                  LinkedIn profile
                </a>
              )}
              {d.person.email && <a href={`mailto:${d.person.email}`}>{d.person.email}</a>}
            </div>
          )}

          {d.relevance.length > 0 && (
            <div className="stack" style={{ gap: 8 }}>
              <div className="section-title">Relevant to your goals</div>
              {d.relevance.map((r: any) => (
                <div className="why why-goal" key={r.goal.id}>
                  <strong>{r.goal.text}</strong>: {r.reason}
                </div>
              ))}
            </div>
          )}

          <form
            className="stack note-form"
            onSubmit={(e) => {
              e.preventDefault()
              save()
            }}
          >
            <div className="section-title">Add a note</div>
            <div className="find-form">
              <input
                className="input"
                value={note}
                maxLength={500}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Hiring an intern, wants a cofounder, met at DevFest..."
                aria-label={`Note about ${d.person.name}`}
              />
              <button className="btn btn-primary" disabled={!note.trim() || saving}>
                Save
              </button>
            </div>
            <p className="tiny faint">Notes stay in your graph. Find searches them, so this person shows up when you look for what they want.</p>
          </form>

          {['intent', 'promise', 'fact'].map((kind) => {
            const items = d.facts.filter((f: any) => f.fact.kind === kind)
            if (!items.length) return null
            return (
              <div key={kind}>
                <div className="section-title">{KIND_TITLE[kind]}</div>
                {items.map((f: any) => (
                  <div className="fact" key={f.fact.id}>
                    <span>{f.fact.text}</span>
                    <span className="tiny faint">
                      {f.source.kind === 'note' ? 'Your note' : `From ${f.source.title}`}, {formatDate(f.source.created_at)}
                      {f.source.kind === 'note' && (
                        <>
                          {' '}
                          <button className="link-button tiny" onClick={() => remove(f.fact.id)}>
                            Remove
                          </button>
                        </>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            )
          })}

          {d.ties.length > 0 && (
            <div>
              <div className="section-title">Connected to</div>
              {d.ties.map((t: any) => (
                <div className="fact" key={t.person.id}>
                  <span>
                    {t.person.name}
                    {t.relation ? `, ${t.relation}` : ''}
                  </span>
                  {t.person.headline && <span className="tiny faint">{t.person.headline}</span>}
                </div>
              ))}
            </div>
          )}

          <div>
            <div className="section-title">Where this came from</div>
            {d.receipts.map((r: any) => (
              <div className="receipt" key={r.source.id}>
                {r.source.kind === 'linkedin'
                  ? `Kept from your LinkedIn export${r.source.filename ? ` (${r.source.filename.split(':').pop()})` : ''}`
                  : r.source.kind === 'card'
                    ? r.source.title
                    : `Memo: ${r.source.title}`}
                <div className="receipt-src">{formatDate(r.source.created_at)}</div>
              </div>
            ))}
          </div>
          <button className="btn btn-quiet" onClick={onClose}>
            Close
          </button>
        </>
      )}
    </Sheet>
  )
}
