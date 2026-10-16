import { useCallback, useEffect, useRef, useState } from 'react'
import { api, formatDate } from '../api'
import { Spinner, useToast } from '../components/ui'

type Result = {
  source: { id: string; title: string }
  people: { id: string; name: string; created: boolean; met: boolean }[]
  facts: { id: string; kind: string; text: string; due?: string }[]
  ties: { a: string; b: string; relation: string }[]
  transcript?: string
}

type Promise_ = { fact: { id: string; text: string; due: string; owner: string }; people: { id: string; name: string }[]; source: { title: string } }
type Source = { source: { id: string; kind: string; title: string; created_at: number; text?: string }; people: number; facts: number }

export function CaptureView({ llm }: { llm: boolean }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Result | null>(null)
  const [promises, setPromises] = useState<Promise_[]>([])
  const [memos, setMemos] = useState<Source[]>([])
  const [recording, setRecording] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const recorder = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const toast = useToast()

  const refresh = useCallback(async () => {
    const [p, s] = await Promise.all([api('/api/promises'), api('/api/sources')])
    setPromises(p.promises)
    setMemos(s.sources.filter((x: Source) => x.source.kind === 'memo'))
  }, [])

  useEffect(() => {
    refresh().catch((e) => toast(e.message))
  }, [refresh, toast])

  useEffect(() => {
    if (!recording) return
    const t = window.setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => window.clearInterval(t)
  }, [recording])

  const save = async () => {
    setBusy(true)
    try {
      setResult(await api<Result>('/api/capture', { body: { text } }))
      setText('')
      refresh()
    } catch (e: any) {
      toast(e.message)
    } finally {
      setBusy(false)
    }
  }

  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream)
      chunks.current = []
      rec.ondataavailable = (e) => e.data.size && chunks.current.push(e.data)
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        const blob = new Blob(chunks.current, { type: rec.mimeType || 'audio/webm' })
        const ext = (rec.mimeType || 'audio/webm').includes('mp4') ? 'm4a' : 'webm'
        const form = new FormData()
        form.append('file', blob, `memo.${ext}`)
        setBusy(true)
        try {
          setResult(await api<Result>('/api/capture/audio', { body: form }))
          refresh()
        } catch (e: any) {
          toast(e.message)
        } finally {
          setBusy(false)
        }
      }
      recorder.current = rec
      rec.start()
      setSeconds(0)
      setRecording(true)
    } catch {
      toast('Microphone access was blocked. Type the memo instead.')
    }
  }

  const stop = () => {
    recorder.current?.stop()
    setRecording(false)
  }

  const markDone = async (id: string) => {
    setPromises((ps) => ps.filter((p) => p.fact.id !== id))
    await api(`/api/promises/${id}`, { body: { done: true } }).catch((e) => toast(e.message))
  }

  const remove = async (id: string) => {
    if (!window.confirm('Delete this memo and everything it added?')) return
    const r = await api(`/api/sources/${id}`, { method: 'DELETE' })
    toast(`Deleted the memo, ${r.facts} notes and ${r.people} people only it mentioned`)
    if (result?.source.id === id) setResult(null)
    refresh()
  }

  const added = result?.people.filter((p) => p.created).length ?? 0
  const linked = (result?.people.length ?? 0) - added

  return (
    <div className="view">
      <div className="view-head">
        <h1>Capture</h1>
        <p className="muted">Right after you meet someone: who they are, what they want, what you promised.</p>
      </div>

      <div className="panel stack">
        <div className="recorder">
          {recording ? (
            <>
              <span className="rec-dot" aria-hidden />
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                Recording {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}
              </span>
              <button className="btn btn-primary btn-small" onClick={stop} style={{ marginLeft: 'auto' }}>
                Stop and save
              </button>
            </>
          ) : (
            <>
              <button className="btn btn-quiet" onClick={start} disabled={busy || !llm}>
                Record a voice memo
              </button>
              {!llm && <span className="tiny faint">Voice needs an OpenAI key on the server</span>}
            </>
          )}
        </div>
        <div className="field">
          <label htmlFor="memo">Or type it</label>
          <textarea
            id="memo"
            className="textarea"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Met Priya Raman at the climate meetup. She's an associate at Monsoon Climate Fund and wants to hire a summer intern. I'll send her my resume by Friday."
          />
        </div>
        <button className="btn btn-primary" onClick={save} disabled={busy || !text.trim()}>
          {busy ? <Spinner label="Saving memo" /> : 'Save memo'}
        </button>
      </div>

      {result && (
        <div className="result" role="status">
          <h3>{result.source.title}</h3>
          {result.transcript && <p className="small muted">"{result.transcript}"</p>}
          <p>
            {added > 0 && `Added ${added} ${added === 1 ? 'person' : 'people'}. `}
            {linked > 0 && `Updated ${linked} you already had. `}
            {result.facts.length} {result.facts.length === 1 ? 'note' : 'notes'}
            {result.ties.length ? `, ${result.ties.length} connection between people` : ''}.
          </p>
          <ul className="list stack" style={{ gap: 6 }}>
            {result.facts.map((f) => (
              <li key={f.id} className="small">
                <span className="chip" style={{ marginRight: 6 }}>
                  {f.kind === 'intent' ? 'wants' : f.kind === 'promise' ? 'promise' : 'note'}
                </span>
                {f.text}
              </li>
            ))}
          </ul>
        </div>
      )}

      {promises.length > 0 && (
        <section className="panel">
          <div className="section-title">Promises you made</div>
          {promises.map((p) => (
            <label className="promise" key={p.fact.id}>
              <input type="checkbox" onChange={() => markDone(p.fact.id)} />
              <span>
                {p.fact.text}
                <span className="tiny faint" style={{ display: 'block' }}>
                  {p.fact.due ? `Due ${p.fact.due}. ` : ''}
                  {p.people.map((x) => x.name).join(', ')}
                </span>
              </span>
            </label>
          ))}
        </section>
      )}

      {memos.length > 0 && (
        <section className="panel">
          <div className="section-title">Memos</div>
          {memos.map((m) => (
            <div className="fact" key={m.source.id}>
              <div className="row spread">
                <strong>{m.source.title}</strong>
                <button className="btn btn-ghost btn-small" onClick={() => remove(m.source.id)}>
                  Delete
                </button>
              </div>
              <span className="tiny faint">
                {formatDate(m.source.created_at)}, {m.people} {m.people === 1 ? 'person' : 'people'}, {m.facts} notes
              </span>
            </div>
          ))}
        </section>
      )}
    </div>
  )
}
