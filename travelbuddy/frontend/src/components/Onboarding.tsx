import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Check, Pencil, RotateCcw, Send } from 'lucide-react'
import { api, userErrorMessage } from '../services/api'
import type { CharacterProfile } from '../types'
import { Mascot } from './Mascot'
import { Button, ChatBubble, ErrorState, LoadingState } from './UI'

const quickReplies = [
  ['Slow mornings, deep days', 'Balanced and flexible', 'Pack it all in'],
  ['Street-food curious', 'A thoughtful mix', 'Keep food familiar'],
  ['Save smart, splurge rarely', 'Flexible for the right thing', 'Comfort comes first'],
  ['Hidden local corners', 'Iconic sights + local life', 'High-energy hotspots'],
  ['Nature and wide horizons', 'Cities, culture, and design', 'A bit of everything'],
  ['Long transfers', 'Crowds and queues', 'Rigid group tours'],
]

export function Onboarding({ onComplete }: { onComplete: (profile: CharacterProfile) => void }) {
  const [messages, setMessages] = useState<Array<{ from: 'tavi' | 'user'; text: string }>>([])
  const [answer, setAnswer] = useState('')
  const [answers, setAnswers] = useState(0)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [profile, setProfile] = useState<CharacterProfile | null>(null)
  const started = useRef(false)

  useEffect(() => { if (!started.current) { started.current = true; void ask('') } }, [])

  async function ask(message: string) {
    setBusy(true); setError('')
    if (message) { setMessages((items) => [...items, { from: 'user', text: message }]); setAnswers((value) => value + 1) }
    try {
      const response = await api.profileChat(message)
      setMessages((items) => [...items, { from: 'tavi', text: response.reply }])
      if (response.done) setProfile(await api.profile())
    } catch (reason) { setError(userErrorMessage(reason, 'Tavi lost the thread for a moment.')) }
    finally { setBusy(false); setAnswer('') }
  }

  async function retake() {
    setBusy(true); setError('')
    try { await api.resetProfile(); setMessages([]); setProfile(null); setAnswers(0); started.current = true; await ask('') }
    catch (reason) { setError(userErrorMessage(reason, 'Could not restart the conversation.')) }
    finally { setBusy(false) }
  }

  if (profile) return <main className="profile-reveal page-stage">
    <div className="profile-reveal__map" aria-hidden="true" />
    <Mascot state="celebrating" size="hero" />
    <span className="eyebrow"><Check /> Your travel character is mapped</span>
    <h1>“I think I’ve got you.”</h1>
    <article className="profile-paper"><span>CHARACTER.MD · VERSION 01</span><p>{profile.summary.replace('# Character Sketch', '').replace(/keywords:.*\n?/i, '').trim()}</p></article>
    <div className="trait-preview">
      <span>PACE · {profile.traits.pace}</span><span>ADVENTURE · {Math.round(profile.traits.adventureLevel * 100)}%</span><span>LOCAL · {Math.round(profile.traits.localVsTourist * 100)}%</span>
    </div>
    <div className="button-row"><Button onClick={() => onComplete(profile)}>That feels like me <ArrowRight /></Button><Button variant="secondary" onClick={retake}><RotateCcw /> Retake</Button></div>
  </main>

  return <main className="onboarding page-stage">
    <header className="onboarding__header"><div><span className="eyebrow">Meet your co-pilot</span><h1>A quick chat, then we roam.</h1></div><div className="progress-dots" aria-label={`Question ${Math.min(answers + 1, 6)} of 6`}>{[0,1,2,3,4,5].map((dot) => <span key={dot} className={dot <= answers ? 'is-active' : ''} />)}</div></header>
    <section className="conversation-shell">
      <aside className="conversation-mascot"><div className="mascot-orbit" /><Mascot state={busy ? 'thinking' : 'greeting'} size="hero" /><p>TAVI · TRAVEL COMPANION</p></aside>
      <div className="conversation-panel">
        <div className="messages" aria-live="polite">
          {messages.map((message, index) => <ChatBubble from={message.from} key={`${index}-${message.text}`}>{message.text}</ChatBubble>)}
          {busy && messages.length > 0 && <div className="typing"><i /><i /><i /></div>}
        </div>
        {error ? <ErrorState message={error} onRetry={() => ask('')} /> : <>
          {!busy && <div className="quick-replies">{(quickReplies[Math.min(answers, 5)] ?? []).map((reply) => <button key={reply} onClick={() => ask(reply)}>{reply}</button>)}</div>}
          <form className="message-composer" onSubmit={(event) => { event.preventDefault(); if (answer.trim()) void ask(answer.trim()) }}>
            <Pencil /><input value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder="Or say it in your own words…" disabled={busy} aria-label="Your answer" /><button aria-label="Send answer" disabled={busy || !answer.trim()}><Send /></button>
          </form>
        </>}
      </div>
    </section>
    {busy && messages.length === 0 && <LoadingState title="Tavi is arriving" detail="Dusting off the compass…" />}
  </main>
}
