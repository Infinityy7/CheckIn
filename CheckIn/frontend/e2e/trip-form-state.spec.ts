import { expect, test, type Page, type Route } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const shots = process.env.TRAVELBUDDY_E2E_ARTIFACTS
  ? path.resolve(process.env.TRAVELBUDDY_E2E_ARTIFACTS)
  : path.resolve(process.cwd(), '../screenshots')
fs.mkdirSync(shots, { recursive: true })

const me = { email: 'vedant@example.com', username: 'vedant', name: 'Vedant', phone: null, intake_complete: true, cotravellers: [] }
const profile = {
  id: 'character:e2e', version: 1,
  summary: 'Vedant is an energetic, moderately budget-conscious traveler who prioritizes memorable experiences over luxury.',
  characterMd: '# Character Sketch\n\nVedant follows local food and flexible schedules.',
  weights: { schemaVersion: 1, vibeWeights: { food: .35, culture: .25 }, spontaneity: .7, chronotype: 'mid', splurgeCategory: 'food', saveCategory: 'transport', archetype: 'foodie_explorer', defaultParty: 'partner', foodAdventurousness: .86, dealBreakers: [], dietaryRequirements: [] },
  rawAnswers: { spontaneity: .7 }, createdAt: '2026-07-14T00:00:00Z', updatedAt: '2026-07-14T00:00:00Z',
}
const health = {
  status: 'ok', account: { status: 'ready', code: null }, gateway: { enabled: false, mode: 'direct' },
  research_cache: null, queue_timeouts: 0, routes: {},
}
const unrealistic = {
  verdict: 'unrealistic', confidence: .9, reason: 'Flights from Delhi to Tokyo alone would exceed a 200 USD budget.',
  suggestion_text: 'Try at least 1800 USD for this route.', suggested_changes: { budget_amount: 1800, end_date: null, destination: null },
}
const hotel = {
  id: 'hotel-1', name: 'Shinjuku Quiet Stay', category: 'hotel', rank: 1, score: .94, description: 'A calm base near the station.',
  reasoning: 'Balances price and pace for your profile.', estimated_cost: '$140 / night', cost_min: 120, cost_max: 160, rating: 4.7, review_count: 980,
  location: 'Shinjuku, Tokyo', image_search_query: 'Shinjuku hotel', metadata: {}, score_breakdown: { rating: .9, vibes: .9, budget: .8, taste: .9 },
}
const agentNames = ['Accommodation Agent', 'Activities Agent', 'Restaurant Agent', 'Transport Agent']

function json(route: Route, body: unknown, status = 200) { return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }) }
function sse(route: Route, events: unknown[]) {
  return route.fulfill({ status: 200, contentType: 'text/event-stream', body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('') })
}

interface CreateCall { key: string; acknowledged: boolean; destination: string; budget: number; usernames: string[] }

async function session(page: Page) {
  await page.addInitScript(() => localStorage.setItem('travelbuddy.session', 'e2e-token'))
  await page.route('**/api/auth/me', (route) => json(route, me))
  await page.route('**/api/profile', (route) => json(route, { sketch: profile.summary, cotravellers: [] }))
  await page.route('**/api/profile/character', (route) => json(route, profile))
  await page.route('**/api/health/agents', (route) => json(route, health))
  await page.route('**/api/trips/pending-check-in', (route) => json(route, { trip: null }))
  await page.route('**/api/users/lookup*', (route) => json(route, { username: 'priya_k', name: 'Priya Kapoor', intake_complete: true, link_status: 'accepted' }))
}

function recordCreate(route: Route, calls: CreateCall[]) {
  const body = route.request().postDataJSON() as { destination: string; budget_amount: number; cotraveller_usernames: string[]; feasibility_acknowledged?: boolean }
  calls.push({
    key: route.request().headers()['idempotency-key'] ?? '',
    acknowledged: body.feasibility_acknowledged === true,
    destination: body.destination, budget: body.budget_amount, usernames: body.cotraveller_usernames,
  })
}

async function openPlanner(page: Page, width: number, height: number) {
  await page.setViewportSize({ width, height })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /point the compass/ })).toBeVisible()
}

async function fillTokyoTrip(page: Page) {
  await page.getByLabel('Destination').fill('Tokyo, Japan')
  await page.getByLabel('Starting from').fill('Delhi, India')
  await page.getByLabel('Amount').fill('200')
  await page.getByLabel('Add by username').fill('priya_k')
  await page.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(page.getByText('@priya_k · Priya Kapoor')).toBeVisible()
}

async function expectTokyoTrip(page: Page) {
  await expect(page.getByLabel('Destination')).toHaveValue('Tokyo, Japan')
  await expect(page.getByLabel('Starting from')).toHaveValue('Delhi, India')
  await expect(page.getByLabel('Amount')).toHaveValue('200')
  await expect(page.getByText('@priya_k · Priya Kapoor')).toBeVisible()
}

async function expectNoHorizontalOverflow(page: Page, width: number) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
}

const viewports = [
  { name: 'desktop', width: 1280, height: 800 },
  { name: 'mobile', width: 390, height: 844 },
]

for (const { name, width, height } of viewports) {
  test.describe(`trip form state · ${name}`, () => {
    test('a feasibility hold keeps every value in place, and Research anyway replays the same idempotency key', async ({ page }) => {
      await session(page)
      const calls: CreateCall[] = []
      let release!: () => void
      const gate = new Promise<void>((resolve) => { release = resolve })
      await page.route('**/api/trip/preferences', async (route) => {
        recordCreate(route, calls)
        if (calls.length === 1) { await gate; return json(route, { trip_id: null, status: 'held', replayed: false, feasibility: unrealistic }) }
        return json(route, { trip_id: 'trip-held', status: 'received', replayed: false, feasibility: unrealistic })
      })
      await page.route('**/api/trip/trip-held/research', (route) => sse(route, [
        { event: 'research_started', agents: agentNames },
        { event: 'agent_completed', agent: 'Accommodation Agent', results: [hotel] },
        { event: 'all_complete', completed: 1, failed: 0, status: 'complete' },
      ]))
      await page.route('**/api/trip/trip-held/cart', (route) => json(route, { tripId: 'trip-held', state: 'open', items: [], checkedAt: new Date().toISOString() }))
      await page.route('**/api/trip/trip-held', (route) => json(route, {
        trip_id: 'trip-held', selections: [], research_results: [{ agent_name: 'Accommodation Agent', recommendations: [hotel] }],
        preferences: { destination: 'Tokyo, Japan', origin: 'Delhi, India', start_date: '2026-10-12', end_date: '2026-10-18', budget_amount: 200, currency: 'USD', vibes: ['culture'], group_type: 'couple', num_travelers: 2, cotravellers: [], cotraveller_usernames: ['priya_k'] },
      }))

      await openPlanner(page, width, height)
      await fillTokyoTrip(page)
      await page.getByRole('button', { name: /Research my trip/ }).click()

      await expect(page.getByRole('button', { name: /Opening a workspace/ })).toBeDisabled()
      await expect(page.getByText(/your details stay right here/)).toBeVisible()
      await expect(page.getByText('Mapping the possibilities')).toHaveCount(0)
      await expectTokyoTrip(page)
      await expectNoHorizontalOverflow(page, width)

      release()
      await expect(page.getByText(/won’t fit its budget/)).toBeVisible()
      await expect(page.getByText(/Flights from Delhi to Tokyo alone would exceed/)).toBeVisible()
      await expectTokyoTrip(page)
      await expect(page.getByRole('button', { name: /Research my trip/ })).toBeEnabled()
      await expectNoHorizontalOverflow(page, width)
      await page.screenshot({ path: `${shots}/14-feasibility-hold-${name}.png`, fullPage: true })

      await page.getByRole('button', { name: 'Research anyway' }).click()
      await expect(page.getByRole('heading', { name: 'The shortlist' })).toBeVisible()
      await expect(page.getByText('Shinjuku Quiet Stay')).toBeVisible()
      expect(calls).toHaveLength(2)
      expect(calls[0].key).toMatch(/^[A-Za-z0-9._:-]{8,128}$/)
      expect(calls[1].key).toBe(calls[0].key)
      expect(calls[0]).toMatchObject({ acknowledged: false, destination: 'Tokyo, Japan', budget: 200, usernames: ['priya_k'] })
      expect(calls[1]).toMatchObject({ acknowledged: true, destination: 'Tokyo, Japan', budget: 200, usernames: ['priya_k'] })
      expect(await page.evaluate(() => localStorage.getItem('travelbuddy.tripDraft.v1'))).toBeNull()
      expect(await page.evaluate(() => localStorage.getItem('travelbuddy.lastTrip'))).toBe('trip-held')
    })

    test('a 422 keeps the form, reuses the key on retry, and the draft survives a reload', async ({ page }) => {
      await session(page)
      const calls: CreateCall[] = []
      await page.route('**/api/trip/preferences', (route) => {
        recordCreate(route, calls)
        return json(route, { detail: 'Trips must start today or later.' }, 422)
      })

      await openPlanner(page, width, height)
      await fillTokyoTrip(page)
      await page.getByRole('button', { name: /Research my trip/ }).click()

      await expect(page.getByRole('alert')).toContainText('Trips must start today or later.')
      await expectTokyoTrip(page)
      await expect(page.getByRole('button', { name: /Research my trip/ })).toBeEnabled()
      await expect(page.getByText('Mapping the possibilities')).toHaveCount(0)
      await expectNoHorizontalOverflow(page, width)

      await page.getByRole('button', { name: /Research my trip/ }).click()
      await expect(page.getByRole('alert')).toContainText('Trips must start today or later.')
      expect(calls).toHaveLength(2)
      expect(calls[1].key).toBe(calls[0].key)
      expect(calls[1].acknowledged).toBe(false)

      await page.reload()
      await expect(page.getByRole('heading', { name: /point the compass/ })).toBeVisible()
      await expectTokyoTrip(page)
      await expectNoHorizontalOverflow(page, width)
    })

    test('a network failure keeps the form and New trip clears the saved draft', async ({ page }) => {
      await session(page)
      await page.route('**/api/trip/preferences', (route) => route.abort('connectionrefused'))

      await openPlanner(page, width, height)
      await fillTokyoTrip(page)
      await page.getByRole('button', { name: /Research my trip/ }).click()

      await expect(page.getByRole('alert')).toContainText('could not reach the server')
      await expectTokyoTrip(page)
      await expect(page.getByRole('button', { name: /Research my trip/ })).toBeEnabled()
      await expectNoHorizontalOverflow(page, width)
      expect(await page.evaluate(() => localStorage.getItem('travelbuddy.tripDraft.v1'))).toContain('Tokyo, Japan')

      // The nav "New trip" button is hidden by shell.css at widths of 820px and below.
      if (width > 820) {
        await page.getByRole('button', { name: /New trip/ }).click()
        await expect(page.getByLabel('Destination')).toHaveValue('')
        expect(await page.evaluate(() => localStorage.getItem('travelbuddy.tripDraft.v1'))).toBeNull()
      }
    })
  })
}
