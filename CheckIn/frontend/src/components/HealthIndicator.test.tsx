import { fireEvent, render, screen } from '@testing-library/react'
import { HealthIndicator } from './HealthIndicator'
import type { AgentHealth, AgentRouteHealth } from '../types'

const route = (circuit: AgentRouteHealth['circuit']): AgentRouteHealth => ({
  attempts: 4, successes: 3, failures: 1, failover_attempts: 0, failover_successes: 0,
  short_circuits: 0, pause_continuations: 0, refusals: 0, input_tokens: 1200, output_tokens: 600,
  cache_read_tokens: 0, in_flight: 0, average_latency_ms: 900, circuit,
  consecutive_failures: circuit === 'closed' ? 0 : 3,
})

const healthy: AgentHealth = {
  status: 'ok',
  account: { status: 'ready', code: null },
  gateway: { enabled: true, mode: 'anthropic_passthrough' },
  research_cache: { enabled: true, hits: 12, misses: 30, stores: 8, errors: 0 },
  queue_timeouts: 0,
  routes: { primary: route('closed'), fallback: route('half_open'), fallback_2: route('open') },
}

it('shows a muted dot with a Status unknown label before the first health check', () => {
  const { container } = render(<HealthIndicator health={null} onRefresh={vi.fn()} />)
  expect(screen.getByText('Status unknown')).toBeInTheDocument()
  const dot = container.querySelector('.status-dot')
  expect(dot).toBeInTheDocument()
  expect(dot).not.toHaveClass('status-dot--ok')
  expect(dot).not.toHaveClass('status-dot--degraded')
  expect(dot).not.toHaveClass('status-dot--unavailable')
})

it('toggles a popover with plain-words status, account, gateway, cache, and circuit chips', () => {
  render(<HealthIndicator health={healthy} onRefresh={vi.fn()} />)
  const trigger = screen.getByRole('button', { name: /Research status/ })
  expect(trigger).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

  fireEvent.click(trigger)
  expect(trigger).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByRole('dialog', { name: /Research status details/ })).toBeInTheDocument()
  expect(screen.getByText('Research running normally')).toBeInTheDocument()
  expect(screen.getByText('Ready')).toBeInTheDocument()
  expect(screen.getByText('Via gateway (anthropic passthrough)')).toBeInTheDocument()
  expect(screen.getByText('Cache: 12 hits · 30 misses')).toBeInTheDocument()
  expect(screen.getByText('primary · closed')).toHaveClass('chip--ok')
  expect(screen.getByText('fallback · half-open')).toHaveClass('chip--warn')
  expect(screen.getByText('fallback 2 · open')).toHaveClass('chip--danger')

  fireEvent.click(trigger)
  expect(trigger).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('reflects degraded and unavailable states in the dot and the status line', () => {
  const { container, rerender } = render(<HealthIndicator health={{ ...healthy, status: 'degraded' }} onRefresh={vi.fn()} />)
  expect(container.querySelector('.status-dot--degraded')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Research status/ }))
  expect(screen.getByText('Research is degraded — retries may be slower')).toBeInTheDocument()

  rerender(<HealthIndicator health={{ ...healthy, status: 'unavailable', gateway: { enabled: false, mode: 'direct' }, research_cache: null }} onRefresh={vi.fn()} />)
  expect(container.querySelector('.status-dot--unavailable')).toBeInTheDocument()
  expect(screen.getByText('Research is unavailable right now')).toBeInTheDocument()
  expect(screen.getByText('Direct to provider')).toBeInTheDocument()
  expect(screen.getByText('Cache disabled')).toBeInTheDocument()
})

it('calls onRefresh from the popover refresh button', () => {
  const refresh = vi.fn()
  render(<HealthIndicator health={healthy} onRefresh={refresh} />)
  fireEvent.click(screen.getByRole('button', { name: /Research status/ }))
  fireEvent.click(screen.getByRole('button', { name: /Refresh status/ }))
  expect(refresh).toHaveBeenCalledTimes(1)
})
