import '../styles/itinerary.css'
import { useState } from 'react'
import { Check, Star } from 'lucide-react'
import type { PostTripState } from '../types'
import { userErrorMessage } from '../services/api'
import { Button, Chip } from './UI'

const ratings = [1, 2, 3, 4, 5] as const

const formatDelta = (delta: number) => `${delta > 0 ? '+' : ''}${delta.toFixed(2)}`

export function PostTripCheckIn({ state, onSubmit }: { state?: PostTripState; onSubmit: (rating: 1 | 2 | 3 | 4 | 5) => Promise<void> }) {
  const [rating, setRating] = useState<1 | 2 | 3 | 4 | 5 | undefined>(state?.rating)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  if (!state?.eligible) return null

  async function submit() {
    if (!rating) return
    setSaving(true); setError('')
    try { await onSubmit(rating) }
    catch (reason) { setError(userErrorMessage(reason, 'Your rating did not save. It is safe to try again.')) }
    finally { setSaving(false) }
  }

  if (state.rating) return <section className="post-trip-checkin post-trip-checkin--complete" aria-live="polite">
    <div className="post-trip-checkin__icon"><Check /></div>
    <div>
      <span className="eyebrow">Post-trip check-in</span>
      <h2>Your profile learned from this trip.</h2>
      <p>You rated it {state.rating} out of 5{state.adjustments?.length ? ` · ${state.adjustments.length} preference ${state.adjustments.length === 1 ? 'weight' : 'weights'} gently tuned` : ''}.</p>
      {state.adjustments?.length ? <div className="taste-adjustments">
        <h3>What Tavi learned</h3>
        <ul>{state.adjustments.map((row) => <li key={row.key}>
          <span className="taste-adjustments__vibe">{row.key}</span>
          <Chip tone={row.delta >= 0 ? 'ok' : 'warn'} title={`${row.key} weight moved from ${row.before.toFixed(2)} to ${row.after.toFixed(2)}`}>{formatDelta(row.delta)}</Chip>
        </li>)}</ul>
      </div> : null}
    </div>
  </section>

  return <section className="post-trip-checkin" aria-labelledby="post-trip-title">
    <div><span className="eyebrow">Post-trip check-in</span><h2 id="post-trip-title">How did this trip feel?</h2><p>Your saved choices and this rating will gently tune future recommendations.</p></div>
    <fieldset disabled={saving}><legend className="sr-only">Rate this trip from 1 to 5</legend><div className="star-rating">{ratings.map((value) => <label key={value}>
      <input type="radio" name="post-trip-rating" value={value} checked={rating === value} onChange={() => setRating(value)} />
      <Star aria-hidden="true" fill={rating && value <= rating ? 'currentColor' : 'none'} />
      <span className="sr-only">{value} out of 5</span>
    </label>)}</div><div className="rating-labels" aria-hidden="true"><span>Not for me</span><span>Perfect</span></div></fieldset>
    {error && <p className="form-error" role="alert">{error}</p>}
    <Button onClick={submit} disabled={!rating || saving}>{saving ? 'Saving…' : 'Save my rating'}</Button>
  </section>
}
