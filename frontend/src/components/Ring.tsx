import { initials } from '../api'

export type Tie = 'close' | 'known' | 'via' | 'none'

/** Initials inside a ring. The ring says how you know them:
 * filled marigold = inner circle, solid = you know them, dashed = through someone. */
export function Avatar({ name, tie = 'known', size = 44 }: { name: string; tie?: Tie; size?: number }) {
  return (
    <span className={`avatar avatar-${tie}`} style={{ width: size, height: size, fontSize: size * 0.36 }} aria-hidden>
      {initials(name)}
    </span>
  )
}

export function tieOf(strength?: string | null, via?: string | null): Tie {
  if (strength === 'close') return 'close'
  if (strength) return 'known'
  if (via) return 'via'
  return 'none'
}

/** Progress or score as an arc. */
export function Meter({
  value,
  max = 100,
  size = 44,
  stroke = 4,
  label,
  tone = 'ink',
}: {
  value: number
  max?: number
  size?: number
  stroke?: number
  label?: string
  tone?: 'ink' | 'keep' | 'close'
}) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const frac = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0
  return (
    <span className={`meter meter-${tone}`} style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} className="meter-track" strokeWidth={stroke} fill="none" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          className="meter-fill"
          strokeWidth={stroke}
          fill="none"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - frac)}
          strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      {label !== undefined && <span className="meter-label">{label}</span>}
    </span>
  )
}

export function Wordmark({ size = 22 }: { size?: number }) {
  return (
    <span className="wordmark" style={{ fontSize: size }}>
      <svg width={size * 1.05} height={size * 1.05} viewBox="0 0 32 32" aria-hidden>
        <circle cx="16" cy="16" r="12.5" fill="none" stroke="currentColor" strokeWidth="3" />
        <circle cx="16" cy="16" r="6" fill="var(--close)" />
        <circle cx="25.6" cy="8.4" r="3.2" fill="var(--keep)" />
      </svg>
      Cirql
    </span>
  )
}
