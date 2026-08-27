import '../styles/itinerary.css'
import { useState } from 'react'
import { ArrowDown, ArrowUp, Bike, CalendarDays, Clock3, Coffee, Download, Hotel, MapPin, Share2, Sparkles, TrainFront } from 'lucide-react'
import type { Itinerary, ItineraryItem, PostTripState, TripPreferences } from '../types'
import { Mascot } from './Mascot'
import { PostTripCheckIn } from './PostTripCheckIn'
import { Button } from './UI'

const iconFor = (category: string) => category === 'restaurant' ? Coffee : category === 'transport' ? TrainFront : category === 'accommodation' ? Hotel : Bike
const dayStamp = (dayNumber: number) => String(dayNumber).padStart(2, '0')

export function ItineraryView({ itinerary, preferences, postTrip, onBack, onRate }: { itinerary: Itinerary; preferences: TripPreferences; postTrip?: PostTripState; onBack: () => void; onRate: (rating: 1 | 2 | 3 | 4 | 5) => Promise<void> }) {
  const [plan, setPlan] = useState(itinerary)
  function move(dayIndex: number, itemIndex: number, delta: number) {
    const target = itemIndex + delta
    if (target < 0 || target >= plan.days[dayIndex].items.length) return
    const days = plan.days.map((day) => ({ ...day, items: [...day.items] }))
    const [item] = days[dayIndex].items.splice(itemIndex, 1); days[dayIndex].items.splice(target, 0, item)
    setPlan({ ...plan, days })
  }
  return <main className="itinerary page-stage">
    <section className="itinerary-cover corner-tick">
      <div className="itinerary-cover__stamp"><span>CHECKIN · TRAVEL DOCUMENT</span><b>FINAL ROUTE</b></div>
      <div>
        <span className="eyebrow">{preferences.start_date} — {preferences.end_date}</span>
        <h1>{plan.trip_title}</h1>
        <p>{plan.trip_summary}</p>
        <div className="itinerary-cover__meta">
          <span><CalendarDays aria-hidden /> {plan.days.length} days</span>
          <span><MapPin aria-hidden /> {preferences.destination}</span>
          <span>{preferences.currency} {preferences.budget_amount.toLocaleString()} budget</span>
        </div>
      </div>
      <div className="itinerary-cover__mascot"><Mascot state="celebrating" size="lg" /><p>Route checked. Breathing room included.</p></div>
    </section>
    <div className="itinerary-toolbar">
      <Button variant="quiet" onClick={onBack}>← Back to shortlist</Button>
      <div>
        <Button variant="secondary" onClick={() => navigator.clipboard?.writeText(window.location.href)}><Share2 aria-hidden /> Share</Button>
        <Button onClick={() => window.print()}><Download aria-hidden /> Print / save PDF</Button>
      </div>
    </div>
    <section className="itinerary-body">
      <aside className="itinerary-index">
        <span className="eyebrow">Trip index</span>
        {plan.days.map((day) => <a href={`#day-${day.day_number}`} key={day.day_number}><b>{dayStamp(day.day_number)}</b><span>{day.theme}</span></a>)}
        <div className="pacing-note"><Sparkles aria-hidden /><p><strong>Tavi’s pacing note</strong>{plan.days.some((d) => d.items.length > 5) ? 'One day is fairly full. Consider moving the final stop into free time.' : 'This route keeps a comfortable rhythm with room for detours.'}</p></div>
      </aside>
      <div className="day-plans">{plan.days.map((day, dayIndex) => <article className="day-plan" id={`day-${day.day_number}`} key={day.day_number}>
        <header><span>DAY {dayStamp(day.day_number)}</span><div><small>{day.date}</small><h2>{day.theme}</h2></div></header>
        <div className="timeline">{day.items.map((item: ItineraryItem, itemIndex) => { const Icon = iconFor(item.category); return <div className="timeline-item" key={`${item.time_slot}-${item.title}`}>
          <div className="timeline-item__rail"><span><Icon aria-hidden /></span><i /></div>
          <div className="timeline-item__time"><Clock3 aria-hidden /> {item.time_slot}</div>
          <div className="timeline-item__card">
            <div>
              <span className="category-label">{item.category}</span>
              <h3>{item.title}</h3>
              <p>{item.description}</p>
              <div className="timeline-item__meta"><span><MapPin aria-hidden /> {item.location}</span><b>{item.cost_estimate}</b></div>
              {item.tip && <blockquote>Local note · {item.tip}</blockquote>}
            </div>
            <div className="reorder">
              <button aria-label="Move earlier" onClick={() => move(dayIndex, itemIndex, -1)} disabled={itemIndex === 0}><ArrowUp aria-hidden /></button>
              <button aria-label="Move later" onClick={() => move(dayIndex, itemIndex, 1)} disabled={itemIndex === day.items.length - 1}><ArrowDown aria-hidden /></button>
            </div>
          </div>
        </div>})}</div>
      </article>)}</div>
    </section>
    <PostTripCheckIn state={postTrip} onSubmit={onRate} />
    <footer className="travel-document-footer"><Mascot state="idle" size="sm" /><div><strong>Built around your character profile</strong><p>Recommendations were ranked for pace, budget, comfort, food curiosity, and local-vs-iconic preference.</p></div><span>TRAVEL WELL · STAY CURIOUS</span></footer>
  </main>
}
