import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api } from '../services/api'
import type { TripCart as TripCartModel } from '../types'
import { TripCart } from './TripCart'

afterEach(() => vi.restoreAllMocks())

it('plainly distinguishes saved choices from supplier-held inventory', () => {
  const cart: TripCartModel = {
    tripId: 'trip-1', state: 'open', checkedAt: new Date().toISOString(), savedExpiresAt: new Date(Date.now() + 60 * 60_000).toISOString(), items: [
      { id: 'saved', recommendationId: 'hotel-1', ratePlanId: 'rate-1', kind: 'hotel', title: 'Garden room', status: 'saved', total: { amount: 420, currency: 'USD' } },
      { id: 'held', recommendationId: 'hotel-2', ratePlanId: 'rate-2', kind: 'hotel', title: 'Corner suite', status: 'held', holdExpiresAt: new Date(Date.now() + 10 * 60_000).toISOString() },
    ],
  }
  render(<TripCart tripId="trip-1" cart={cart} loading={false} error="" onCartChange={vi.fn()} onError={vi.fn()} />)

  expect(screen.getByText('Saved · not reserved')).toBeInTheDocument()
  expect(screen.getByText('Supplier-held')).toBeInTheDocument()
  expect(screen.getByText(/Saved choice · no inventory hold/i)).toBeInTheDocument()
  expect(screen.getByText(/Supplier hold ·/i)).toBeInTheDocument()
  expect(screen.getByText(/Saved cart · not reserved/i)).toHaveTextContent('does not reserve inventory')
})

it('revalidates every cart item before checkout', async () => {
  const before: TripCartModel = { tripId: 'trip-1', state: 'open', checkedAt: new Date().toISOString(), items: [{ id: 'saved', recommendationId: 'hotel-1', kind: 'hotel', title: 'Garden room', status: 'quoted' }] }
  const after: TripCartModel = { ...before, state: 'ready', items: [{ ...before.items[0], status: 'price_changed', message: 'Now $25 more' }] }
  vi.spyOn(api, 'revalidateCart').mockResolvedValue(after)
  const onCartChange = vi.fn()
  render(<TripCart tripId="trip-1" cart={before} loading={false} error="" onCartChange={onCartChange} onError={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Recheck all prices/i }))
  await waitFor(() => expect(onCartChange).toHaveBeenCalledWith(after))
})

it('removes an individual cart item through the typed cart service', async () => {
  const before: TripCartModel = { tripId: 'trip-1', state: 'open', checkedAt: new Date().toISOString(), items: [{ id: 'saved', recommendationId: 'hotel-1', kind: 'hotel', title: 'Garden room', status: 'saved' }] }
  const after: TripCartModel = { ...before, items: [] }
  const remove = vi.spyOn(api, 'removeCartItem').mockResolvedValue(after)
  const onCartChange = vi.fn()
  render(<TripCart tripId="trip-1" cart={before} loading={false} error="" onCartChange={onCartChange} onError={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Remove Garden room from cart/i }))
  await waitFor(() => expect(remove).toHaveBeenCalledWith('trip-1', 'saved'))
  expect(onCartChange).toHaveBeenCalledWith(after)
})
