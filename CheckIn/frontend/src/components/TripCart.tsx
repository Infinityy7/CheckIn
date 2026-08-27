import '../styles/inventory.css'
import { AlertTriangle, Check, Clock3, RefreshCw, ShoppingBag, X } from 'lucide-react'
import { useState } from 'react'
import { api, userErrorMessage } from '../services/api'
import type { Money, TripCart as TripCartModel, TripCartItem } from '../types'
import { Chip, Countdown, SourceBadge, type ChipTone } from './UI'

function money(value?: Money) {
  if (!value) return ''
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: value.currency, maximumFractionDigits: 2 }).format(value.amount)
}

const statusCopy: Record<TripCartItem['status'], string> = {
  saved: 'Saved · not reserved', quoted: 'Price quoted', held: 'Supplier-held', revalidating: 'Checking again', booking: 'Booking', booked: 'Booked', confirmed: 'Confirmed',
  price_changed: 'Price changed', unavailable: 'Unavailable', expired: 'Expired', error: 'Needs attention',
}

const statusTone: Record<TripCartItem['status'], ChipTone> = {
  saved: 'muted', quoted: 'info', held: 'gold', revalidating: 'info', booking: 'info', booked: 'ok', confirmed: 'ok',
  price_changed: 'danger', unavailable: 'danger', expired: 'danger', error: 'danger',
}

/** Each supplier clock is shown as its own honest timer; a plain save has no hold to count down. */
function ItemClock({ item }: { item: TripCartItem }) {
  if (item.holdExpiresAt) return <Countdown expiresAt={item.holdExpiresAt} label="Hold expires in" expiredLabel="Hold expired — recheck prices" />
  if (item.quoteExpiresAt) return <Countdown expiresAt={item.quoteExpiresAt} label="Quote expires in" expiredLabel="Quote expired — recheck prices" />
  return <span className="cart-item__no-hold"><Clock3 aria-hidden /> Saved choice · no inventory hold</span>
}

export function TripCart({ tripId, cart, loading, error, onCartChange, onError }: {
  tripId: string
  cart: TripCartModel | null
  loading: boolean
  error: string
  onCartChange: (cart: TripCartModel) => void
  onError: (message: string) => void
}) {
  const [removing, setRemoving] = useState<string[]>([])

  async function remove(item: TripCartItem) {
    setRemoving((items) => [...items, item.id]); onError('')
    try { onCartChange(await api.removeCartItem(tripId, item.id)) }
    catch (reason) { onError(userErrorMessage(reason, `${item.title} could not be removed.`)) }
    finally { setRemoving((items) => items.filter((id) => id !== item.id)) }
  }

  async function revalidate() {
    onError('')
    try { onCartChange(await api.revalidateCart(tripId)) }
    catch (reason) { onError(userErrorMessage(reason, 'The cart could not be refreshed.')) }
  }

  return <section className="booking-cart" aria-label="Booking cart">
    <header><span><ShoppingBag /> Booking cart</span><small>{cart?.items.length ?? 0} items</small></header>
    <p className="cart-truth">Saved choices are not reservations. The saved-cart timer only clears this shortlist; it does not reserve inventory. Provider quotes and holds have their own timers below.</p>
    {cart?.savedExpiresAt && <div className="saved-cart-expiry">
      <Countdown expiresAt={cart.savedExpiresAt} label="Cart clears in" expiredLabel="Cart expired — saved choices cleared" />
      <small>Clears this shortlist only · nothing is reserved</small>
    </div>}
    {loading && <div className="cart-loading" role="status">Loading saved choices…</div>}
    {!loading && error && <p className="cart-error" role="alert"><AlertTriangle /> {error}</p>}
    {!loading && !error && !cart?.items.length && <p className="cart-empty">Open a hotel’s room prices, then save the exact rate you want.</p>}
    {cart?.items.length ? <div className="cart-items">{cart.items.map((item) => <article className={`cart-item cart-item--${item.status}`} key={item.id}>
      <div className="cart-item__head">
        <Chip tone={statusTone[item.status]} icon={item.status === 'confirmed' || item.status === 'booked' ? <Check aria-hidden /> : <Clock3 aria-hidden />}>{statusCopy[item.status]}</Chip>
        {item.total && <strong>{money(item.total)}</strong>}
      </div>
      <h4>{item.title}</h4>{item.subtitle && <p>{item.subtitle}</p>}
      <div className="cart-item__meta">
        {item.sourceMode && <SourceBadge sourceMode={item.sourceMode} isLive={item.isLive} />}
        <ItemClock item={item} />
      </div>
      {item.message && <small>{item.message}</small>}
      <button className="cart-item__remove" type="button" onClick={() => void remove(item)} disabled={removing.includes(item.id)} aria-label={`Remove ${item.title} from cart`}><X /> {removing.includes(item.id) ? 'Removing…' : 'Remove'}</button>
    </article>)}</div> : null}
    {cart?.items.length ? <button className="cart-refresh" type="button" onClick={revalidate} disabled={cart.state === 'revalidating'} aria-busy={cart.state === 'revalidating'}><RefreshCw aria-hidden /> {cart.state === 'revalidating' ? 'Checking…' : 'Recheck all prices'}</button> : null}
  </section>
}
