import { expect, test, type Page, type Route } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const shots = process.env.TRAVELBUDDY_E2E_ARTIFACTS
  ? path.resolve(process.env.TRAVELBUDDY_E2E_ARTIFACTS)
  : path.resolve(process.cwd(), '../screenshots')
fs.mkdirSync(shots, { recursive: true })

const now = Date.now()
const profile = {
  id: 'character:quality', version: 1,
  summary: 'A balanced traveler who values local food, comfortable stays, and enough flexibility to explore.',
  weights: {
    schemaVersion: 1, vibeWeights: { food: .4, culture: .35, nature: .25 }, spontaneity: .6,
    chronotype: 'mid', splurgeCategory: 'stay', saveCategory: 'transport', archetype: 'foodie_explorer',
    defaultParty: 'partner', foodAdventurousness: .8, dealBreakers: [], dietaryRequirements: [],
  },
  rawAnswers: {}, createdAt: '2026-08-01T12:00:00Z', updatedAt: '2026-08-01T12:00:00Z',
}
const preferences = {
  destination: 'Kyoto, Japan', origin: 'Mumbai, India', start_date: '2026-10-12', end_date: '2026-10-18',
  budget_amount: 3200, currency: 'USD', vibes: ['culture', 'food', 'nature'], group_type: 'couple',
  num_travelers: 2, cotravellers: [], cotraveller_usernames: [],
}

function recommendation(category: 'hotel' | 'activity' | 'restaurant' | 'transport', id: string, name: string, rank = 1) {
  return {
    id, name, category, rank, score: .94 - rank * .02, description: `A verified ${category} option for the selected dates.`,
    reasoning: 'It matches the traveler’s pace, comfort, and budget preferences.', estimated_cost: '$120–240',
    cost_min: 120, cost_max: 240, rating: 4.8, review_count: 840, location: 'Central Kyoto',
    image_search_query: `${name} Kyoto`, score_breakdown: { rating: .9, budget: .86, taste: .95 },
    metadata: category === 'transport' ? {
      home_to_airport: 'UberX estimate · pickup timed after flight selection',
      outbound_flight: 'BOM → KIX · 09:40–19:15',
      airport_to_hotel: 'Uber Comfort estimate · KIX → selected hotel',
      return_departure_transfer: 'Hotel pickup timed for the return flight',
      return_flight: 'KIX → BOM · 11:20–22:10',
      airport_to_home: 'Arrival ride prepared after landing',
      daily_transport: 'ICOCA card + local rail',
    } : {},
  }
}

const hotels = [
  recommendation('hotel', 'hotel-1', 'Gion Garden House', 1),
  recommendation('hotel', 'hotel-2', 'Kyoto Riverside', 2),
  recommendation('hotel', 'hotel-3', 'Machiya Lantern Inn', 3),
]
const transport = recommendation('transport', 'transport-1', 'Complete Kyoto Journey')
const trip = {
  trip_id: 'trip-quality', preferences,
  research_results: [
    { agent_name: 'Accommodation Agent', recommendations: hotels },
    { agent_name: 'Transport Agent', recommendations: [transport] },
  ],
  research_errors: [], selections: [],
}
const rates = {
  hotelId: 'supplier-hotel-1', recommendationId: 'hotel-1', checkedAt: new Date(now).toISOString(),
  source: 'Controlled provider fixture', sourceMode: 'test', isLive: false,
  rooms: [
    {
      id: 'deluxe-king', name: 'Deluxe king', description: 'Garden-facing room with a quiet sitting area.',
      occupancy: { adults: 2, children: 1, maxGuests: 3 }, beds: [{ type: 'king', count: 1 }], board: 'Breakfast included',
      ratePlans: [
        {
          id: 'flex-rate', label: 'Flexible breakfast rate', total: { amount: 640, currency: 'USD' },
          nightly: { amount: 290, currency: 'USD' }, taxesAndFees: { amount: 60, currency: 'USD' }, refundable: true,
          cancellationSummary: 'Free cancellation until 48 hours before arrival.', availabilityStatus: 'limited', roomsRemaining: 2,
          quoteExpiresAt: new Date(now + 20 * 60_000).toISOString(), source: 'Controlled provider fixture', sourceMode: 'test', isLive: false,
        },
      ],
    },
  ],
}
const flights = {
  recommendationId: 'transport-1', checkedAt: new Date(now).toISOString(),
  source: 'Controlled flight fixture', sourceMode: 'test', isLive: false,
  offers: [{
    id: 'flight-offer-1', carrier: 'Quality Air', flightNumber: 'QA 101', origin: 'BOM', destination: 'KIX',
    departAt: '2026-10-12T04:10:00Z', arriveAt: '2026-10-12T13:45:00Z', durationMinutes: 575, stops: 0,
    journeyType: 'round_trip', total: { amount: 740, currency: 'USD' }, quoteExpiresAt: new Date(now + 15 * 60_000).toISOString(),
    availabilityStatus: 'available', source: 'Controlled flight fixture', sourceMode: 'test', isLive: false,
  }],
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function mockWorkspace(page: Page) {
  const diagnostics = { consoleErrors: [] as string[], pageErrors: [] as string[], failedRequests: [] as string[] }
  page.on('console', (message) => { if (message.type() === 'error') diagnostics.consoleErrors.push(message.text()) })
  page.on('pageerror', (error) => diagnostics.pageErrors.push(error.message))
  page.on('requestfailed', (request) => diagnostics.failedRequests.push(`${request.method()} ${request.url()}`))
  await page.addInitScript(() => {
    localStorage.setItem('travelbuddy.session', 'quality-token')
    localStorage.setItem('travelbuddy.lastTrip', 'trip-quality')
  })
  await page.route('**/api/auth/me', (route) => json(route, { email: 'quality@example.com', username: 'quality', name: 'Quality Tester', phone: null, intake_complete: true, cotravellers: [] }))
  await page.route('**/api/profile/character', (route) => json(route, profile))
  await page.route('**/api/health/agents', (route) => json(route, {
    status: 'ok', account: { status: 'ready', code: null }, gateway: { enabled: false, mode: 'direct' },
    research_cache: { enabled: true, hits: 2, misses: 3, stores: 3, errors: 0 }, queue_timeouts: 0,
    routes: { primary: { attempts: 12, successes: 12, failures: 0, failover_attempts: 0, failover_successes: 0, short_circuits: 0, pause_continuations: 0, refusals: 0, input_tokens: 4200, output_tokens: 2100, cache_read_tokens: 0, in_flight: 0, average_latency_ms: 800, circuit: 'closed', consecutive_failures: 0 } },
  }))
  await page.route('**/api/trips/pending-check-in', (route) => json(route, { trip: null }))
  await page.route('**/api/trip/trip-quality/cart/revalidate', (route) => json(route, {
    tripId: 'trip-quality', state: 'ready', savedExpiresAt: new Date(now + 60 * 60_000).toISOString(), checkedAt: new Date().toISOString(), items: [{
      id: 'cart-hotel-1', recommendationId: 'hotel-1', ratePlanId: 'flex-rate', kind: 'hotel',
      title: 'Gion Garden House', subtitle: 'Deluxe king · Flexible breakfast rate', status: 'price_changed',
      total: { amount: 640, currency: 'USD' }, quoteExpiresAt: new Date(now + 20 * 60_000).toISOString(),
      source: 'Controlled provider fixture', sourceMode: 'test', isLive: false,
      message: 'The supplier returned a new price. Review it before booking.',
    }],
  }))
  await page.route('**/api/trip/trip-quality/cart/items', (route) => {
    const input = route.request().postDataJSON() as { recommendationId: string; ratePlanId?: string; kind: 'hotel' | 'flight' | 'ride' | 'restaurant' }
    const isFlight = input.kind === 'flight'
    return json(route, {
      tripId: 'trip-quality', state: 'open', savedExpiresAt: new Date(now + 60 * 60_000).toISOString(), checkedAt: new Date().toISOString(), items: [{
        id: isFlight ? 'cart-flight-1' : 'cart-hotel-1', recommendationId: input.recommendationId,
        ratePlanId: input.ratePlanId, kind: input.kind, title: isFlight ? 'Quality Air QA 101' : 'Gion Garden House',
        subtitle: isFlight ? 'BOM → KIX · direct' : 'Deluxe king · Flexible breakfast rate', status: 'quoted',
        total: { amount: isFlight ? 740 : 640, currency: 'USD' }, quoteExpiresAt: new Date(now + (isFlight ? 15 : 20) * 60_000).toISOString(),
        source: isFlight ? 'Controlled flight fixture' : 'Controlled provider fixture', sourceMode: 'test', isLive: false,
      }],
    })
  })
  await page.route('**/api/trip/trip-quality/cart/items/*', (route) => json(route, {
    tripId: 'trip-quality', state: 'open', savedExpiresAt: new Date(now + 60 * 60_000).toISOString(),
    checkedAt: new Date().toISOString(), items: [],
  }))
  await page.route('**/api/trip/trip-quality/cart', (route) => json(route, {
    tripId: 'trip-quality', state: 'open', savedExpiresAt: new Date(now + 60 * 60_000).toISOString(),
    checkedAt: new Date().toISOString(), items: [],
  }))
  await page.route('**/api/trip/trip-quality/hotels/*/rates', (route) => {
    const recommendationId = /\/hotels\/([^/]+)\/rates/.exec(route.request().url())?.[1] ?? 'hotel-1'
    return json(route, { ...rates, recommendationId })
  })
  await page.route('**/api/trip/trip-quality/flights/transport-1/offers', (route) => json(route, flights))
  await page.route('**/api/trip/trip-quality', (route) => json(route, trip))
  return diagnostics
}

test('controlled hotel rates move into the cart without claiming a reservation', async ({ page }) => {
  const diagnostics = await mockWorkspace(page)
  await page.goto('/')

  await page.getByRole('tab', { name: /Stays/ }).click()
  await expect(page.getByText('Gion Garden House')).toBeVisible()
  await page.getByRole('button', { name: /Rooms & booking prices/i }).first().click()
  await expect(page.getByText('Deluxe king')).toBeVisible()
  await expect(page.getByText('Test inventory · non-live').first()).toBeVisible()
  await expect(page.getByText('via Controlled provider fixture')).toBeVisible()
  await expect(page.getByText('$640')).toBeVisible()
  await page.getByRole('button', { name: /Save quoted rate/i }).click()
  await expect(page.getByText('Price quoted')).toBeVisible()
  await expect(page.locator('.cart-truth')).toContainText('does not reserve inventory')
  await expect(page.getByText(/Cart clears in/)).toBeVisible()
  await expect(page.getByText('Clears this shortlist only · nothing is reserved')).toBeVisible()
  await page.evaluate(() => {
    window.scrollTo(0, 0)
    const nav = document.querySelector<HTMLElement>('.app-nav')
    if (nav) nav.style.position = 'static'
  })
  await page.screenshot({ path: `${shots}/08-hotel-room-rates-cart.png`, fullPage: true })
  await page.getByRole('button', { name: /Recheck all prices/i }).click()
  await expect(page.getByText('Price changed')).toBeVisible()
  await page.getByRole('button', { name: /Remove Gion Garden House from cart/i }).click()
  await expect(page.getByText(/Open a hotel’s room prices/i)).toBeVisible()

  expect(diagnostics.consoleErrors).toEqual([])
  expect(diagnostics.pageErrors).toEqual([])
  expect(diagnostics.failedRequests).toEqual([])
})

test('door-to-door transport is complete and dependency-aware', async ({ page }) => {
  const diagnostics = await mockWorkspace(page)
  await page.goto('/')

  await expect(page.getByText('Complete journey')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Outbound' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Return' })).toBeVisible()
  await expect(page.getByText('Home → Mumbai, India airport')).toBeVisible()
  await expect(page.getByText('Airport → selected hotel')).toBeVisible()
  await expect(page.getByText('Daily mobility')).toBeVisible()
  await page.getByRole('button', { name: /Flight times & fares/i }).click()
  await expect(page.getByText('Test inventory · non-live').first()).toBeVisible()
  await expect(page.getByText('via Controlled flight fixture')).toBeVisible()
  await expect(page.getByText('Quality Air · QA 101')).toBeVisible()
  await expect(page.getByText('$740')).toBeVisible()
  await page.getByRole('button', { name: /Save flight quote/i }).click()
  await expect(page.getByRole('button', { name: /Added/i })).toBeDisabled()
  await expect(page.locator('.cart-truth')).toContainText('does not reserve inventory')
  await page.evaluate(() => {
    window.scrollTo(0, 0)
    const nav = document.querySelector<HTMLElement>('.app-nav')
    if (nav) nav.style.position = 'static'
  })
  await page.screenshot({ path: `${shots}/09-door-to-door-flights.png`, fullPage: true })

  expect(diagnostics.consoleErrors).toEqual([])
  expect(diagnostics.pageErrors).toEqual([])
  expect(diagnostics.failedRequests).toEqual([])
})

for (const viewport of [
  { label: 'mobile', width: 390, height: 844 },
  { label: 'tablet', width: 820, height: 1180 },
  { label: 'desktop', width: 1440, height: 900 },
]) {
  test(`inventory workspace has no horizontal overflow on ${viewport.label}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    const diagnostics = await mockWorkspace(page)
    await page.goto('/')
    await page.getByRole('tab', { name: /Stays/ }).click()
    await page.getByRole('button', { name: /Rooms & booking prices/i }).first().click()
    await expect(page.getByText('Deluxe king')).toBeVisible()

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
    const unnamedButtons = await page.locator('button').evaluateAll((buttons) => buttons.filter((button) => !(button.getAttribute('aria-label') || button.textContent?.trim())).length)
    if (viewport.label === 'mobile') {
      await page.evaluate(() => {
        window.scrollTo(0, 0)
        const nav = document.querySelector<HTMLElement>('.app-nav')
        if (nav) nav.style.position = 'static'
      })
      await page.screenshot({ path: `${shots}/10-hotel-inventory-mobile.png`, fullPage: true })
    }
    expect(unnamedButtons).toBe(0)
    expect(diagnostics.consoleErrors).toEqual([])
    expect(diagnostics.pageErrors).toEqual([])
    expect(diagnostics.failedRequests).toEqual([])
  })
}

test('cart TTL shows an honest countdown and expired quotes are labelled', async ({ page }) => {
  const diagnostics = await mockWorkspace(page)
  await page.route('**/api/trip/trip-quality/cart', (route) => json(route, {
    tripId: 'trip-quality', state: 'open',
    savedExpiresAt: new Date(Date.now() + 10 * 60_000).toISOString(),
    checkedAt: new Date().toISOString(),
    items: [
      {
        id: 'cart-hotel-1', recommendationId: 'hotel-1', ratePlanId: 'flex-rate', kind: 'hotel',
        title: 'Gion Garden House', subtitle: 'Deluxe king · Flexible breakfast rate', status: 'quoted',
        total: { amount: 640, currency: 'USD' }, quoteExpiresAt: new Date(Date.now() + 9 * 60_000).toISOString(),
        source: 'Controlled provider fixture', sourceMode: 'test', isLive: false,
      },
      {
        id: 'cart-ride-1', recommendationId: 'transport-1', ratePlanId: 'demo-ride', kind: 'ride',
        title: 'Airport transfer sample', subtitle: 'KIX → hotel · sample provider', status: 'expired',
        total: { amount: 45, currency: 'USD' }, quoteExpiresAt: new Date(Date.now() - 5 * 60_000).toISOString(),
        source: 'Demo ride fixture', sourceMode: 'demo', isLive: false,
      },
    ],
  }))
  await page.goto('/')

  await expect(page.getByText(/Cart clears in/)).toBeVisible()
  await expect(page.getByText('Clears this shortlist only · nothing is reserved')).toBeVisible()
  await expect(page.locator('.cart-truth')).toContainText('does not reserve inventory')
  await expect(page.getByText(/Quote expires in/)).toBeVisible()
  await expect(page.getByText('Quote expired — recheck prices')).toBeVisible()
  await expect(page.getByText('Expired', { exact: true })).toBeVisible()
  await expect(page.getByText('Test inventory · non-live')).toBeVisible()
  await expect(page.getByText('Demo · non-live sample')).toBeVisible()

  expect(diagnostics.consoleErrors).toEqual([])
  expect(diagnostics.pageErrors).toEqual([])
  expect(diagnostics.failedRequests).toEqual([])
})
