import { useEffect, useRef, useState } from 'react'
import { Send } from 'lucide-react'
import { api, ApiError, userErrorMessage } from '../services/api'
import { Banner, Button, ChatBubble, Modal } from './UI'
import { Mascot } from './Mascot'

interface Turn {
  from: 'tavi' | 'user'
  text: string
}

interface TurnError {
  message: string
  retryable: boolean
}

/** One submitted answer. The key is minted once and reused on every retry so the server never stores it twice. */
interface PendingTurn {
  message: string
  turnKey?: string
}

function newTurnKey(): string {
  const cryptoApi = globalThis.crypto
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') return cryptoApi.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

/**
 * Four-question companion taste intake, one chat turn per answer.
 * The thread lives on the server: on open it is restored, so a reload or a
 * server restart resumes mid-conversation instead of starting over. The
 * backend saves the sketch itself and answers done=true on the last turn.
 */
export function CompanionIntake({ name, onProfiled, onClose }: {
  name: string
  onProfiled: (name: string) => void
  onClose: () => void
}) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(true)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<TurnError | null>(null)
  const [pending, setPending] = useState<PendingTurn | null>(null)
  const startedRef = useRef(false)
  const threadRef = useRef<HTMLDivElement>(null)

  const deliver = async (turn: PendingTurn) => {
    setBusy(true)
    setError(null)
    setPending(turn)
    try {
      const { reply, done: finished } = turn.turnKey
        ? await api.profileChat(turn.message, name, turn.turnKey)
        : await api.profileChat(turn.message, name)
      setTurns((previous) => [...previous, { from: 'tavi', text: reply }])
      setPending(null)
      if (finished) {
        setDone(true)
        onProfiled(name)
      }
    } catch (reason) {
      setError({
        message: userErrorMessage(reason, 'The intake chat hit turbulence.'),
        retryable: reason instanceof ApiError ? reason.retryable : true,
      })
    } finally {
      setBusy(false)
    }
  }

  const restore = async () => {
    const transcript = await api.profileChatTranscript(name)
      .catch((): { turns: Turn[]; done: boolean } => ({ turns: [], done: false }))
    setTurns(transcript.turns)
    if (transcript.done) {
      setDone(true)
      setBusy(false)
      return
    }
    const last = transcript.turns[transcript.turns.length - 1]
    if (!last || last.from === 'user') {
      await deliver({ message: '' })
      return
    }
    setBusy(false)
  }

  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    void restore()
  })

  useEffect(() => {
    const node = threadRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [turns, busy])

  const send = () => {
    const text = draft.trim()
    if (!text || busy || done) return
    setTurns((previous) => [...previous, { from: 'user', text }])
    setDraft('')
    void deliver({ message: text, turnKey: newTurnKey() })
  }

  return <Modal open title={`Taste intake · ${name}`} eyebrow="Travel party" onClose={onClose}>
    <div className="intake">
      <p className="intake-lede">Four quick questions about how <strong>{name}</strong> travels. Tavi folds their taste into the group ranking.</p>
      <div className="intake-thread" role="log" aria-live="polite" aria-label={`Taste intake conversation for ${name}`} ref={threadRef}>
        {turns.map((turn, index) => <ChatBubble key={index} from={turn.from}>{turn.text}</ChatBubble>)}
        {busy && <ChatBubble from="tavi">
          <span className="typing" aria-hidden><i /><i /><i /></span>
          <span className="sr-only">Tavi is thinking…</span>
        </ChatBubble>}
      </div>
      {error && <Banner
        tone={error.retryable ? 'warn' : 'danger'}
        title={error.retryable ? 'That didn’t go through' : 'The intake can’t continue'}
        detail={error.message}
        action={error.retryable && pending !== null
          ? <Button type="button" variant="secondary" onClick={() => void deliver(pending)}>Try again</Button>
          : undefined}
      />}
      {done
        ? <div className="intake-success" role="status">
          <Mascot state="celebrating" size="lg" label={`Tavi celebrating ${name}’s finished intake`} />
          <h3>{name} is on the map</h3>
          <p>Taste sketch saved — rankings will balance the whole group now.</p>
          <Button type="button" onClick={onClose}>Back to the trip</Button>
        </div>
        : <div className="intake-composer">
          <label htmlFor="companion-intake-answer">Answer as {name}</label>
          <div className="intake-composer__row">
            <input
              id="companion-intake-answer"
              value={draft}
              disabled={busy}
              autoComplete="off"
              placeholder={busy ? 'Tavi is thinking…' : 'Type their answer'}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); send() } }}
            />
            <Button type="button" onClick={send} disabled={busy || !draft.trim()}><Send aria-hidden /> Send</Button>
          </div>
        </div>}
    </div>
  </Modal>
}
