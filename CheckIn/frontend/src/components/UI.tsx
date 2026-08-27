import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { AlertCircle, AlertTriangle, Archive, Check, ChevronRight, CircleCheck, Clock3, Info, LoaderCircle, Moon, Sun, X } from 'lucide-react'
import { Mascot } from './Mascot'

export function Brand() {
  return <div className="brand"><span className="brand__mark">CI</span><span>CheckIn</span></div>
}

export function Button({ children, variant = 'primary', className = '', ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'quiet' | 'danger' }) {
  return <button className={`button button--${variant} ${className}`} {...props}>{children}</button>
}

export function ChatBubble({ children, from = 'tavi' }: { children: ReactNode; from?: 'tavi' | 'user' }) {
  return <div className={`chat-bubble chat-bubble--${from}`}>{children}</div>
}

export function LoadingState({ title = 'Mapping the possibilities', detail = 'Tavi is keeping the route warm.' }: { title?: string; detail?: string }) {
  return <div className="state-card"><Mascot state="thinking" size="lg" /><LoaderCircle className="spin" /><h3>{title}</h3><p>{detail}</p></div>
}

export function EmptyState({ title, detail, action }: { title: string; detail: string; action?: ReactNode }) {
  return <div className="state-card"><Mascot state="idle" size="lg" /><h3>{title}</h3><p>{detail}</p>{action}</div>
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <div className="state-card state-card--error"><Mascot state="confused" size="lg" /><AlertTriangle /><h3>A route went fuzzy</h3><p>{message}</p>{onRetry && <Button onClick={onRetry}>Try that again</Button>}</div>
}

export function useFocusTrap(open: boolean, onClose: () => void, containerRef: RefObject<HTMLElement | null>, initialRef?: RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    initialRef?.current?.focus()
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key !== 'Tab' || !containerRef.current) return
      const focusable = [...containerRef.current.querySelectorAll<HTMLElement>('button, input, textarea, select, [href], [tabindex]:not([tabindex="-1"])')].filter((item) => !item.hasAttribute('disabled'))
      if (!focusable.length) return
      const first = focusable[0]; const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => { document.removeEventListener('keydown', onKeyDown); previous?.focus() }
  }, [open, onClose, containerRef, initialRef])
}

export function Drawer({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: ReactNode }) {
  const drawerRef = useRef<HTMLElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  useFocusTrap(open, onClose, drawerRef, closeRef)
  if (!open) return null
  return <div className="drawer-layer" role="dialog" aria-modal="true" aria-label={title}>
    <button className="drawer-layer__scrim" aria-label="Close" onClick={onClose} />
    <section className="drawer" ref={drawerRef}>
      <header><div><span className="eyebrow">Character profile</span><h2>{title}</h2></div><button ref={closeRef} className="icon-button" aria-label="Close profile" onClick={onClose}><X /></button></header>
      {children}
    </section>
  </div>
}

export function Toast({ children, success = false }: { children: ReactNode; success?: boolean }) {
  return <div className={`toast ${success ? 'toast--success' : ''}`}>{success && <Check />}{children}</div>
}

export type ChipTone = 'neutral' | 'ok' | 'warn' | 'danger' | 'info' | 'gold' | 'brand' | 'cached' | 'muted'

export function Chip({ tone = 'neutral', icon, children, title }: { tone?: ChipTone; icon?: ReactNode; children: ReactNode; title?: string }) {
  return <span className={`chip ${tone === 'neutral' ? '' : `chip--${tone}`}`} title={title}>{icon}{children}</span>
}

export function formatAge(seconds: number): string {
  if (seconds < 90) return 'just now'
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`
  return `${Math.round(seconds / 3600)}h ago`
}

/** Cached research must never read as a fresh live quote: dashed chip + age + match. */
export function CachedBadge({ ageSeconds, similarity }: { ageSeconds: number; similarity?: number }) {
  return <Chip tone="cached" icon={<Archive aria-hidden />} title="Served from CheckIn's research cache, not a fresh live search">
    Cached {formatAge(ageSeconds)}{typeof similarity === 'number' ? ` · ${Math.round(similarity * 100)}% taste match` : ''}
  </Chip>
}

export function SourceBadge({ sourceMode, isLive }: { sourceMode?: string; isLive?: boolean }) {
  if (!sourceMode || sourceMode === 'unavailable') return null
  if (isLive && sourceMode === 'live') return <Chip tone="ok" icon={<CircleCheck aria-hidden />}>Live inventory</Chip>
  return <Chip tone="warn" icon={<AlertCircle aria-hidden />}>{sourceMode === 'demo' ? 'Demo · non-live sample' : 'Test inventory · non-live'}</Chip>
}

function formatRemaining(seconds: number): string {
  if (seconds >= 5400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  if (seconds >= 120) return `${Math.floor(seconds / 60)}m`
  return `${Math.max(0, Math.floor(seconds / 60))}:${String(Math.max(0, seconds % 60)).padStart(2, '0')}`
}

export function Countdown({ expiresAt, label, expiredLabel }: { expiresAt?: string; label: string; expiredLabel?: string }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!expiresAt) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [expiresAt])
  if (!expiresAt) return null
  const left = Math.floor((new Date(expiresAt).getTime() - now) / 1000)
  if (left <= 0) return <span className="countdown countdown--expired" role="status"><Clock3 aria-hidden /> {expiredLabel ?? `${label} expired`}</span>
  return <span className={`countdown ${left > 600 ? 'countdown--calm' : ''}`} role="timer"><Clock3 aria-hidden /> {label} {formatRemaining(left)}</span>
}

export function Banner({ tone, title, detail, action, children }: { tone: 'warn' | 'danger' | 'info' | 'ok'; title: string; detail?: string; action?: ReactNode; children?: ReactNode }) {
  const Icon = tone === 'danger' ? AlertTriangle : tone === 'warn' ? AlertCircle : tone === 'ok' ? CircleCheck : Info
  return <div className={`banner banner--${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
    <Icon aria-hidden />
    <div className="banner__body"><strong>{title}</strong>{detail && <p>{detail}</p>}{children}</div>
    {action}
  </div>
}

export function Meter({ label, value }: { label: string; value: number }) {
  const percent = Math.round(Math.max(0, Math.min(1, value)) * 100)
  return <div className="meter"><span>{label}</span><span className="meter__track" role="meter" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-label={label}><i className="meter__fill" style={{ width: `${percent}%` }} /></span><span>{percent}%</span></div>
}

export function SegmentedControl<T extends string>({ options, value, onChange, ariaLabel, format }: { options: readonly T[]; value: T; onChange: (next: T) => void; ariaLabel: string; format?: (option: T) => string }) {
  return <div className="seg" role="group" aria-label={ariaLabel}>
    {options.map((option) => <button type="button" key={option} aria-pressed={value === option} onClick={() => onChange(option)}>{format ? format(option) : option}</button>)}
  </div>
}

export function Stepper({ steps, current }: { steps: readonly string[]; current: number }) {
  return <nav className="stepper" aria-label="Trip progress">
    {steps.map((step, index) => <span key={step} style={{ display: 'contents' }}>
      {index > 0 && <ChevronRight aria-hidden />}
      <span className={`stepper__step ${index < current ? 'stepper__step--done' : ''}`} aria-current={index === current ? 'step' : undefined}>
        <i>{index < current ? <Check aria-hidden /> : index + 1}</i>{step}
      </span>
    </span>)}
  </nav>
}

export function Modal({ open, title, eyebrow, onClose, children }: { open: boolean; title: string; eyebrow?: string; onClose: () => void; children: ReactNode }) {
  const modalRef = useRef<HTMLElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  useFocusTrap(open, onClose, modalRef, closeRef)
  if (!open) return null
  return <div className="modal-layer" role="dialog" aria-modal="true" aria-label={title}>
    <button className="modal-layer__scrim" aria-label="Close" onClick={onClose} />
    <section className="modal" ref={modalRef}>
      <header><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h2>{title}</h2></div><button ref={closeRef} className="icon-button" aria-label={`Close ${title}`} onClick={onClose}><X /></button></header>
      <div className="modal__body">{children}</div>
    </section>
  </div>
}

export type Theme = 'light' | 'dark'

export function activeTheme(): Theme {
  const stored = document.documentElement.dataset.theme
  if (stored === 'light' || stored === 'dark') return stored
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => activeTheme())
  const toggle = useCallback(() => {
    const next: Theme = activeTheme() === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.theme = next
    try { localStorage.setItem('checkin.theme', next) } catch { /* private mode */ }
    setTheme(next)
  }, [])
  return <button className="theme-toggle" onClick={toggle} aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}>
    {theme === 'dark' ? <Sun aria-hidden /> : <Moon aria-hidden />}
  </button>
}
