/** Thumbs up / down on a suggestion. "Wrong" removes it and teaches the next ranking. */
export function Rate({ vote, onVote, compact = false }: { vote?: string | null; onVote: (v: 'good' | 'wrong' | 'clear') => void; compact?: boolean }) {
  return (
    <span className={`rate ${compact ? 'rate-compact' : ''}`} role="group" aria-label="Rate this suggestion">
      <button
        className={`rate-btn ${vote === 'good' ? 'on' : ''}`}
        aria-pressed={vote === 'good'}
        onClick={() => onVote(vote === 'good' ? 'clear' : 'good')}
        title="Good fit"
      >
        <svg viewBox="0 0 24 24" aria-hidden>
          <path d="M7 11v9H4v-9h3zm2 9h7.5a2 2 0 0 0 2-1.6l1.3-6A2 2 0 0 0 17.8 10H14V6a2 2 0 0 0-2-2l-3 6v10z" />
        </svg>
        {!compact && 'Good fit'}
      </button>
      <button className={`rate-btn wrong ${vote === 'wrong' ? 'on' : ''}`} aria-pressed={vote === 'wrong'} onClick={() => onVote('wrong')} title="Wrong suggestion">
        <svg viewBox="0 0 24 24" aria-hidden>
          <path d="M17 13V4h3v9h-3zm-2-9H7.5a2 2 0 0 0-2 1.6l-1.3 6A2 2 0 0 0 6.2 14H10v4a2 2 0 0 0 2 2l3-6V4z" />
        </svg>
        {!compact && 'Wrong'}
      </button>
    </span>
  )
}
