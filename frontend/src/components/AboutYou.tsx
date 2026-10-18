import { useEffect, useState } from 'react'
import { api } from '../api'
import { useToast } from './ui'

/** Who you are, so "internship" means the right internship. Private to your own graph. */
export function AboutYou() {
  const [about, setAbout] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const toast = useToast()
  useEffect(() => {
    api<{ about: string }>('/api/about')
      .then((r) => {
        setAbout(r.about)
        setDraft(r.about)
      })
      .catch(() => setAbout(''))
  }, [])
  if (about === null) return null
  const save = async () => {
    try {
      const r = await api<{ about: string }>('/api/about', { method: 'PUT', body: { about: draft } })
      setAbout(r.about)
      setDraft(r.about)
      toast('Saved. Re-rank a goal to use it.')
    } catch (e: any) {
      toast(e.message)
    }
  }
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="section-title">About you</div>
      <textarea
        className="textarea"
        rows={3}
        maxLength={600}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="SDE student in Bangalore, Python and Go, looking for a remote or UK internship for summer 2027"
        aria-label="About you"
      />
      <div className="row" style={{ gap: 8, alignItems: 'center' }}>
        <p className="tiny faint grow" style={{ margin: 0 }}>
          Private. Goal rankings use it to judge who fits you: role, skills, location, timing.
        </p>
        <button className="btn btn-primary btn-small" onClick={save} disabled={draft.trim() === about}>
          Save
        </button>
      </div>
    </div>
  )
}
