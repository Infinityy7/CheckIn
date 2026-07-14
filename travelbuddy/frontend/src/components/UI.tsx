import type { ReactNode } from 'react'
import { AlertTriangle, Check, LoaderCircle, X } from 'lucide-react'
import { Mascot } from './Mascot'

export function Brand() {
  return <div className="brand"><span className="brand__mark">TB</span><span>TravelBuddy</span></div>
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

export function Drawer({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: ReactNode }) {
  if (!open) return null
  return <div className="drawer-layer" role="dialog" aria-modal="true" aria-label={title}>
    <button className="drawer-layer__scrim" aria-label="Close" onClick={onClose} />
    <section className="drawer">
      <header><div><span className="eyebrow">Character profile</span><h2>{title}</h2></div><button className="icon-button" aria-label="Close profile" onClick={onClose}><X /></button></header>
      {children}
    </section>
  </div>
}

export function Toast({ children, success = false }: { children: ReactNode; success?: boolean }) {
  return <div className={`toast ${success ? 'toast--success' : ''}`}>{success && <Check />}{children}</div>
}
