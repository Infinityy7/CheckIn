import '../styles/ops.css'
import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, X } from 'lucide-react'
import type { AgentHealth, AgentRouteHealth } from '../types'
import { Button, Chip, type ChipTone } from './UI'

const ROUTE_ORDER = ['primary', 'fallback', 'fallback_2']

const statusLine: Record<AgentHealth['status'], string> = {
  ok: 'Research running normally',
  degraded: 'Research is degraded — retries may be slower',
  unavailable: 'Research is unavailable right now',
}

const shortLabel: Record<AgentHealth['status'], string> = {
  ok: 'Research normal',
  degraded: 'Research degraded',
  unavailable: 'Research unavailable',
}

const circuitTone: Record<AgentRouteHealth['circuit'], ChipTone> = { closed: 'ok', half_open: 'warn', open: 'danger' }
const circuitLabel: Record<AgentRouteHealth['circuit'], string> = { closed: 'closed', half_open: 'half-open', open: 'open' }

export function HealthIndicator({ health, onRefresh }: { health: AgentHealth | null; onRefresh: () => void }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const close = useCallback(() => { setOpen(false); triggerRef.current?.focus() }, [])

  useEffect(() => {
    if (!open) return
    function onKeyDown(event: KeyboardEvent) { if (event.key === 'Escape') close() }
    function onPointerDown(event: MouseEvent) { if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('mousedown', onPointerDown)
    return () => { document.removeEventListener('keydown', onKeyDown); document.removeEventListener('mousedown', onPointerDown) }
  }, [open, close])

  const label = health ? shortLabel[health.status] : 'Status unknown'
  const line = health ? statusLine[health.status] : 'Tavi has not heard from the research service yet.'
  const routes = health
    ? Object.entries(health.routes).sort(([a], [b]) => {
        const rank = (name: string) => { const index = ROUTE_ORDER.indexOf(name); return index === -1 ? ROUTE_ORDER.length : index }
        return rank(a) - rank(b)
      })
    : []

  return <div className="health-indicator" ref={rootRef}>
    <button ref={triggerRef} type="button" className="health-indicator__trigger" aria-haspopup="dialog" aria-expanded={open} aria-label={`Research status: ${label}`} onClick={() => setOpen((current) => !current)}>
      <span className={`status-dot${health ? ` status-dot--${health.status}` : ''}`} aria-hidden="true" />
      <span className="health-indicator__label">{label}</span>
    </button>
    {open && <div className="health-popover" role="dialog" aria-label="Research status details">
      <div className="health-popover__head">
        <strong>{line}</strong>
        <button type="button" className="health-popover__close" aria-label="Close status details" onClick={close}><X aria-hidden /></button>
      </div>
      {health ? <>
        <dl>
          <div><dt>Account</dt><dd>{health.account.status === 'ready' ? 'Ready' : `Blocked${health.account.code ? ` · ${health.account.code}` : ''}`}</dd></div>
          <div><dt>Routing</dt><dd>{health.gateway.enabled ? 'Via gateway (anthropic passthrough)' : 'Direct to provider'}</dd></div>
          <div><dt>Cache</dt><dd>{health.research_cache?.enabled ? `Cache: ${health.research_cache.hits} hits · ${health.research_cache.misses} misses` : 'Cache disabled'}</dd></div>
        </dl>
        {routes.length > 0 && <div className="health-popover__circuits">
          {routes.map(([name, route]) => <Chip key={name} tone={circuitTone[route.circuit]} title={`${name.replace(/_/g, ' ')} route circuit is ${circuitLabel[route.circuit]}`}>{name.replace(/_/g, ' ')} · {circuitLabel[route.circuit]}</Chip>)}
        </div>}
      </> : <p className="health-popover__empty">Refresh to check on the research agents.</p>}
      <Button variant="secondary" onClick={onRefresh}><RefreshCw aria-hidden /> Refresh status</Button>
    </div>}
  </div>
}
