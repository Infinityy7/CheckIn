import '../styles/identity.css'
import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, Check, RotateCcw, Send } from 'lucide-react'
import { api, ApiError, userErrorMessage } from '../services/api'
import type { CharacterProfile, IntakeAnswer, IntakeQuestion, IntakeState, Vibe } from '../types'
import { Mascot } from './Mascot'
import { Banner, Button, ChatBubble, ErrorState, LoadingState } from './UI'

const emptyPair = { splurge: '', save: '' }

function initialAnswer(question: IntakeQuestion, saved?: IntakeAnswer): IntakeAnswer {
  if (saved !== undefined) return saved
  if (question.type === 'slider') return 0.5
  if (question.type === 'multi_choice') return []
  if (question.type === 'paired_choice') return emptyPair
  return ''
}

function answerIsValid(question: IntakeQuestion, answer: IntakeAnswer): boolean {
  if (question.optional && answer === '') return true
  if (question.type === 'slider') return typeof answer === 'number' && answer >= 0 && answer <= 1
  if (question.type === 'multi_choice') {
    return Array.isArray(answer) && answer.length >= (question.minSelections ?? 0) && answer.length <= (question.maxSelections ?? Infinity)
  }
  if (question.type === 'paired_choice') {
    return typeof answer === 'object' && !Array.isArray(answer) && answer.splurge.length > 0 && answer.save.length > 0 && answer.splurge !== answer.save
  }
  return typeof answer === 'string' && (question.optional || answer.trim().length > 0)
}

function QuestionControl({ question, value, onChange }: { question: IntakeQuestion; value: IntakeAnswer; onChange: (value: IntakeAnswer) => void }) {
  if (question.type === 'slider') {
    return <div className="idn-slider">
      <input aria-label={question.prompt} type="range" min="0" max="1" step="0.1" value={Number(value)} onChange={(event) => onChange(Number(event.target.value))} />
      <div><span>{question.lowLabel ?? 'Planned'}</span><strong>{Math.round(Number(value) * 100)}%</strong><span>{question.highLabel ?? 'Spontaneous'}</span></div>
    </div>
  }

  if (question.type === 'paired_choice') {
    const pair = typeof value === 'object' && !Array.isArray(value) ? value : emptyPair
    return <div className="idn-paired">
      <label><span>I’d splurge on</span><select value={pair.splurge} onChange={(event) => onChange({ ...pair, splurge: event.target.value })}><option value="">Choose one</option>{question.options?.map((option) => <option key={option.value} value={option.value} disabled={option.value === pair.save}>{option.label}</option>)}</select></label>
      <label><span>I’d save on</span><select value={pair.save} onChange={(event) => onChange({ ...pair, save: event.target.value })}><option value="">Choose one</option>{question.options?.map((option) => <option key={option.value} value={option.value} disabled={option.value === pair.splurge}>{option.label}</option>)}</select></label>
    </div>
  }

  if (question.type === 'free_text') {
    return <label className="idn-free-text"><span className="sr-only">Your answer</span><input maxLength={180} value={typeof value === 'string' ? value : ''} onChange={(event) => onChange(event.target.value)} placeholder="A quiet sunrise, a great meal, and nowhere to rush…" /></label>
  }

  const selected = Array.isArray(value) ? value : []
  return <div className="idn-options" role={question.type === 'single_choice' ? 'radiogroup' : 'group'} aria-label={question.prompt}>
    {question.options?.map((option) => {
      const active = question.type === 'single_choice' ? value === option.value : selected.includes(option.value)
      return <button
        type="button"
        role={question.type === 'single_choice' ? 'radio' : undefined}
        aria-checked={question.type === 'single_choice' ? active : undefined}
        aria-pressed={question.type === 'multi_choice' ? active : undefined}
        className={active ? 'is-active' : ''}
        key={option.value}
        onClick={() => {
          if (question.type === 'single_choice') onChange(option.value)
          else if (active) onChange(selected.filter((item) => item !== option.value))
          else if (selected.length < (question.maxSelections ?? Infinity)) onChange([...selected, option.value])
        }}
      >{option.label}</button>
    })}
  </div>
}

export function Onboarding({ onComplete }: { onComplete: (profile: CharacterProfile) => void }) {
  const [intake, setIntake] = useState<IntakeState | null>(null)
  const [answer, setAnswer] = useState<IntakeAnswer>('')
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [completionError, setCompletionError] = useState('')
  const [profile, setProfile] = useState<CharacterProfile | null>(null)

  async function load() {
    setBusy(true); setError(''); setCompletionError('')
    try {
      const state = await api.intake()
      setIntake(state)
      if (state.profile) setProfile(state.profile)
      else if (state.status === 'complete') setProfile(await api.profile())
      else if (state.currentQuestion) setAnswer(initialAnswer(state.currentQuestion, state.answers[state.currentQuestion.id]))
      else if (state.status === 'completion_failed') setCompletionError('Your answers are saved, but the last sketch attempt did not finish.')
    } catch (reason) { setError(userErrorMessage(reason, 'Tavi could not open your questions.')) }
    finally { setBusy(false) }
  }

  // Intake bootstrap runs once; subsequent loads are explicit retries or resets.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load() }, [])

  async function complete() {
    setBusy(true); setError(''); setCompletionError('')
    setIntake((current) => current ? { ...current, status: 'completing' } : current)
    try { setProfile(await api.completeIntake()) }
    catch (reason) {
      setCompletionError(userErrorMessage(reason, 'Your answers are saved, but Tavi could not finish the sketch.'))
      setIntake((current) => current ? { ...current, status: 'completion_failed', currentQuestion: null } : current)
    }
    finally { setBusy(false) }
  }

  async function submit(value: IntakeAnswer = answer) {
    const question = intake?.currentQuestion
    if (!question || !answerIsValid(question, value)) return
    setBusy(true); setError('')
    try {
      const next = await api.answerIntake(question.id, value)
      setIntake(next)
      if (next.profile) setProfile(next.profile)
      else if (next.status === 'ready_to_complete' || next.currentIndex >= next.total || !next.currentQuestion) await complete()
      else setAnswer(initialAnswer(next.currentQuestion, next.answers[next.currentQuestion.id]))
    } catch (reason) { setError(userErrorMessage(reason, 'That answer did not save. It is safe to try again.')) }
    finally { setBusy(false) }
  }

  async function retake() {
    setBusy(true); setError(''); setCompletionError('')
    try {
      try { await api.resetIntake() }
      catch (reason) { if (!(reason instanceof ApiError) || reason.status !== 404) throw reason; await api.resetProfile() }
      setProfile(null); setIntake(null); await load()
    } catch (reason) { setError(userErrorMessage(reason, 'Could not restart the questionnaire.')) }
    finally { setBusy(false) }
  }

  const topVibes = useMemo(() => Object.entries(profile?.weights?.vibeWeights ?? {}).sort((a, b) => Number(b[1]) - Number(a[1])).slice(0, 3) as Array<[Vibe, number]>, [profile])

  if (profile) return <main className="idn-reveal">
    <Mascot state="celebrating" size="hero" />
    <span className="eyebrow"><Check aria-hidden /> Your travel character is mapped</span>
    <h1>“I think I’ve got you.”</h1>
    <article className="idn-paper"><span>CHARACTER.MD · VERSION {String(profile.version).padStart(2, '0')}</span><p>{profile.summary}</p></article>
    <div className="idn-trait-preview">
      {profile.weights ? <><span>SPONTANEOUS · {Math.round(profile.weights.spontaneity * 100)}%</span>{topVibes.map(([vibe, weight]) => <span key={vibe}>{vibe.toUpperCase()} · {Math.round(weight * 100)}%</span>)}</> : profile.traits ? <><span>PACE · {profile.traits.pace}</span><span>ADVENTURE · {Math.round(profile.traits.adventureLevel * 100)}%</span><span>LOCAL · {Math.round(profile.traits.localVsTourist * 100)}%</span></> : null}
    </div>
    <div className="idn-button-row"><Button onClick={() => onComplete(profile)}>That feels like me <ArrowRight aria-hidden /></Button><Button variant="secondary" onClick={retake}><RotateCcw aria-hidden /> Retake</Button></div>
  </main>

  if (busy && !intake) return <main className="idn-onboarding"><LoadingState title="Tavi is arriving" detail="Opening your nine-question travel quiz…" /></main>
  if (error && !intake) return <main className="idn-onboarding"><ErrorState message={error} onRetry={load} /></main>

  const question = intake?.currentQuestion
  const compiling = intake ? !question : false
  const failed = compiling && (intake?.status === 'completion_failed' || completionError !== '')
  const step = Math.min((intake?.currentIndex ?? 0) + 1, 9)
  const canContinue = question ? answerIsValid(question, answer) : false
  const selectionCount = Array.isArray(answer) ? answer.length : 0

  return <main className="idn-onboarding">
    <header className="idn-onboarding__header">
      <div><span className="eyebrow">Meet your co-pilot</span><h1>A quick quiz, then we roam.</h1></div>
      <div className="idn-progress">
        <span>{compiling ? '9 of 9 answered' : `Question ${step} of 9`}</span>
        <div role="progressbar" aria-valuemin={1} aria-valuemax={9} aria-valuenow={compiling ? 9 : step}><i style={{ width: `${(compiling ? 9 : step) / 9 * 100}%` }} /></div>
      </div>
    </header>
    <section className="idn-shell">
      <aside className="idn-shell__mascot"><Mascot state={busy ? 'thinking' : failed ? 'confused' : 'greeting'} size="hero" /><p>TAVI · TRAVEL COMPANION</p></aside>
      <div className="idn-shell__panel">
        {question ? <>
          <div aria-live="polite"><ChatBubble>{question.prompt}</ChatBubble></div>
          <QuestionControl question={question} value={answer} onChange={setAnswer} />
          {question.type === 'multi_choice' && <p className="idn-counter">{selectionCount} selected{question.maxSelections ? ` · choose ${question.maxSelections}` : ''}</p>}
          {question.type === 'paired_choice' && typeof answer === 'object' && !Array.isArray(answer) && answer.splurge === answer.save && answer.splurge && <p className="form-error" role="alert">Splurge and save categories must be different.</p>}
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="idn-actions"><Button onClick={() => void submit()} disabled={busy || !canContinue}>{busy ? 'Saving…' : step === 9 ? 'Map my character' : 'Next question'} <Send aria-hidden /></Button>{question.optional && <Button variant="quiet" onClick={() => { setAnswer(''); void submit('') }} disabled={busy}>Skip</Button>}</div>
        </> : <div className="idn-compile" role="status">
          <h2>All nine answers are in.</h2>
          <p>Tavi distills your answers into a character sketch — the profile that guides every ranking and hard filter from here on.</p>
          {failed
            ? <Banner tone="danger" title="The sketch could not be compiled" detail={completionError || 'Your answers are saved. It is safe to try again.'} action={<Button onClick={() => void complete()} disabled={busy}>Try completing again</Button>} />
            : <Button onClick={() => void complete()} disabled={busy}>{busy ? 'Compiling your character…' : 'Compile my character'}</Button>}
        </div>}
      </div>
    </section>
  </main>
}
