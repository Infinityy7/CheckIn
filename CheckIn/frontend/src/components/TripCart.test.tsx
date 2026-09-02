import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ApiError, api } from '../services/api'
import type { TripCart as TripCartModel } from '../types'
import { CART_REFRESHED_NOTICE, TripCart } from './TripCart'

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
  expect(screen.getByText(/Hold expires in/i)).toBeInTheDocument()
  expect(screen.getByText(/Saved choices are not reservations/i)).toBeInTheDocument()
})

it('shows the saved-cart TTL countdown from savedExpiresAt without implying a reservation', () => {
  const cart: TripCartModel = {
    tripId: 'trip-1', state: 'open', checkedAt: new Date().toISOString(),
    savedExpiresAt: new Date(Date.now() + 60 * 60_000).toISOString(),
    items: [{ id: 'saved', recommendationId: 'hotel-1', kind: 'hotel', title: 'Garden room', status: 'saved' }],
  }
  render(<TripCart tripId="trip-1" cart={cart} loading={false} error="" onCartChange={vi.fn()} onError={vi.fn()} />)

  expect(screen.getByText(/Cart clears in/i)).toBeInTheDocument()
  expect(screen.getByText(/Clears this shortlist only · nothing is reserved/i)).toBeInTheDocument()
})

it('counts down an item quote and reports honestly once it has expired', () => {
  const cart: TripCartModel = {
    tripId: 'trip-1', state: 'open', checkedAt: new Date().toISOString(), items: [
      { id: 'quoted', recommendationId: 'hotel-1', kind: 'hotel', title: 'Garden room', status: 'quoted', quoteExpiresAt: new Date(Date.now() + 12 * 60_000).toISOString() },
      { id: 'stale', recommendationId: 'hotel-2', kind: 'hotel', title: 'Corner suite', status: 'quoted', quoteExpiresAt: new Date(Date.now() - 60_000).toISOString() },
    ],
  }
  render(<TripCart tripId="trip-1" cart={cart} loading={false} error="" onCartChange={vi.fn()} onError={vi.fn()} />)

  expect(screen.getByText(/Quote expires in/i)).toBeInTheDocument()
  expect(screen.getByText(/Quote expired/i)).toBeInTheDocument()
})

it('revalidates every cart item before checkout', async () => {
  const before: TripCartModel = { tripId: 'trip-1', state: 'open', checkedAt: new Date().toISOString(), items: [{ id: 'saved', recommendationId: 'hotel-1', kind: 'hotel', title: 'Garden room', status: 'quoted' }] }
  const after: TripCartModel = { ...before, state: 'ready', items: [{ ...before.items[0], status: 'price_changed', message: 'Now $25 more' }] }
  vi.spyOn(api, 'revalidateCart').mockResolvedValue(after)
  const onCartChange = vi.fn()
  render(<TripCart tripId="trip-1" cart={before} loading={false} error="" onCartChange={onCartChange} onError={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Recheck all prices/i }))
  await waitFor(() => expect(api.revalidateCart).toHaveBeenCalledWith('trip-1'))
  await waitFor(() => expect(onCartChange).toHaveBeenCalledWith(after))
})

it('shows the revalidate button as busy while the cart is being rechecked', () => {
  const cart: TripCartModel = { tripId: 'trip-1', state: 'revalidating', checkedAt: new Date().toISOString(), items: [{ id: 'saved', recommendationId: 'hotel-1', kind: 'hotel', title: 'Garden room', status: 'revalidating' }] }
  render(<TripCart tripId="trip-1" cart={cart} loading={false} error="" onCartChange={vi.fn()} onError={vi.fn()} />)

  expect(screen.getByRole('button', { name: /Checking…/i })).toBeDisabled()
})

it('removes an individual cart item through the typed cart service, guarding with the cart version', async () => {
  const before: TripCartModel = { tripId: 'trip-1', version: 3, state: 'open', checkedAt: new Date().toISOString(), items: [{ id: 'saved', recommendationId: 'hotel-1', kind: 'hotel', title: 'Garden room', status: 'saved' }] }
  const after: TripCartModel = { ...before, version: 4, items: [] }
  const remove = vi.spyOn(api, 'removeCartItem').mockResolvedValue(after)
  const onCartChange = vi.fn()
  render(<TripCart tripId="trip-1" cart={before} loading={false} error="" onCartChange={onCartChange} onError={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Remove Garden room from cart/i }))
  await waitFor(() => expect(remove).toHaveBeenCalledWith('trip-1', 'saved', 3))
  expect(onCartChange).toHaveBeenCalledWith(after)
})

it('refetches the cart and explains when a removal hits a version conflict', async () => {
  const stale: TripCartModel = { tripId: 'trip-1', version: 2, state: 'open', checkedAt: new Date().toISOString(), items: [{ id: 'saved', recommendationId: 'hotel-1', kind: 'hotel', title: 'Garden room', status: 'saved' }] }
  const fresh: TripCartModel = { ...stale, version: 5, items: [{ ...stale.items[0], title: 'Garden room (rechecked)' }] }
  vi.spyOn(api, 'removeCartItem').mockRejectedValue(new ApiError('Your cart changed elsewhere. Refresh it and try again.', 409, 'CART_VERSION_CONFLICT', 'req-1', true))
  const refetch = vi.spyOn(api, 'cart').mockResolvedValue(fresh)
  const onCartChange = vi.fn(); const onError = vi.fn()
  render(<TripCart tripId="trip-1" cart={stale} loading={false} error="" onCartChange={onCartChange} onError={onError} />)

  fireEvent.click(screen.getByRole('button', { name: /Remove Garden room from cart/i }))

  await waitFor(() => expect(refetch).toHaveBeenCalledWith('trip-1'))
  await waitFor(() => expect(onCartChange).toHaveBeenCalledWith(fresh))
  expect(await screen.findByRole('status')).toHaveTextContent(CART_REFRESHED_NOTICE)
  expect(onError).not.toHaveBeenCalledWith(expect.stringContaining('could not be removed'))
})

it('reports ordinary removal failures through onError', async () => {
  const cart: TripCartModel = { tripId: 'trip-1', version: 1, state: 'open', checkedAt: new Date().toISOString(), items: [{ id: 'saved', recommendationId: 'hotel-1', kind: 'hotel', title: 'Garden room', status: 'saved' }] }
  vi.spyOn(api, 'removeCartItem').mockRejectedValue(new ApiError('That cart item does not exist.', 404, 'NOT_FOUND', 'req-2', false))
  const refetch = vi.spyOn(api, 'cart')
  const onError = vi.fn()
  render(<TripCart tripId="trip-1" cart={cart} loading={false} error="" onCartChange={vi.fn()} onError={onError} />)

  fireEvent.click(screen.getByRole('button', { name: /Remove Garden room from cart/i }))

  await waitFor(() => expect(onError).toHaveBeenCalledWith(expect.stringContaining('That cart item does not exist.')))
  expect(refetch).not.toHaveBeenCalled()
})
