import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Onboarding } from './Onboarding'
import { api, ApiError } from '../services/api'
import type { CharacterProfile, IntakeState } from '../types'

const profile: CharacterProfile = {
  id: 'character:test', version: 2, summary: 'A curious traveler who likes local food and room for discovery.',
  characterMd: '# Character Sketch\n\nA curious traveler.',
  weights: {
    schemaVersion: 1, vibeWeights: { food: .5, culture: .3, nature: .2 }, spontaneity: .7, chronotype: 'mid',
    splurgeCategory: 'food', saveCategory: 'transport', archetype: 'foodie_explorer', defaultParty: 'partner',
    foodAdventurousness: .8, dealBreakers: ['theme_parks'], dietaryRequirements: [],
  },
  rawAnswers: {}, createdAt: '2026-07-01T00:00:00Z', updatedAt: '2026-07-02T00:00:00Z',
}

const first: IntakeState = {
  questionnaireVersion: 'personalisation-v1', status: 'in_progress', currentIndex: 0, total: 9, answers: {},
  currentQuestion: { id: 'spontaneity', prompt: 'Your ideal trip day: every hour planned, or see where the day takes you?', type: 'slider', lowLabel: 'Planned', highLabel: 'Spontaneous' },
}

afterEach(() => vi.restoreAllMocks())

it('renders persisted progress and submits a controlled answer value', async () => {
  vi.spyOn(api, 'intake').mockResolvedValue(first)
  const next: IntakeState = {
    ...first, currentIndex: 1, answers: { spontaneity: .8 },
    currentQuestion: { id: 'top_vibes', prompt: 'Pick your top 3 — what makes a trip unforgettable?', type: 'multi_choice', minSelections: 3, maxSelections: 3, options: [{ value: 'food', label: 'Food' }] },
  }
  const answer = vi.spyOn(api, 'answerIntake').mockResolvedValue(next)

  render(<Onboarding onComplete={vi.fn()} />)
  expect(await screen.findByText('Question 1 of 9')).toBeInTheDocument()
  fireEvent.change(screen.getByRole('slider'), { target: { value: '.8' } })
  fireEvent.click(screen.getByRole('button', { name: /Next question/i }))

  await waitFor(() => expect(answer).toHaveBeenCalledWith('spontaneity', .8))
  expect(await screen.findByText('Question 2 of 9')).toBeInTheDocument()
})

it('shows completion_failed with a retry that calls completeIntake again', async () => {
  const finalQuestion: IntakeState = {
    questionnaireVersion: 'personalisation-v1', status: 'in_progress', currentIndex: 8, total: 9, answers: {},
    currentQuestion: { id: 'perfect_moment', prompt: 'In one line — describe your perfect travel moment.', type: 'free_text', optional: true },
  }
  vi.spyOn(api, 'intake').mockResolvedValue(finalQuestion)
  vi.spyOn(api, 'answerIntake').mockResolvedValue({ ...finalQuestion, status: 'ready_to_complete', currentIndex: 9, currentQuestion: null, answers: { perfect_moment: 'Sunrise and breakfast.' } })
  const complete = vi.spyOn(api, 'completeIntake')
    .mockRejectedValueOnce(new ApiError('Sketch timed out.', 0, 'REQUEST_TIMEOUT'))
    .mockResolvedValueOnce(profile)

  render(<Onboarding onComplete={vi.fn()} />)
  await screen.findByText('Question 9 of 9')
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Sunrise and breakfast.' } })
  fireEvent.click(screen.getByRole('button', { name: /Map my character/i }))

  expect(await screen.findByText(/Sketch timed out/)).toBeInTheDocument()
  expect(screen.getByText('The sketch could not be compiled')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Try completing again/i }))
  expect(await screen.findByRole('heading', { name: /I think I’ve got you/ })).toBeInTheDocument()
  expect(complete).toHaveBeenCalledTimes(2)
})

it('resumes a saved completion_failed intake at the compile step', async () => {
  vi.spyOn(api, 'intake').mockResolvedValue({
    questionnaireVersion: 'personalisation-v1', status: 'completion_failed', currentIndex: 9, total: 9, answers: { perfect_moment: 'Sunrise.' }, currentQuestion: null,
  })
  const complete = vi.spyOn(api, 'completeIntake').mockResolvedValue(profile)

  render(<Onboarding onComplete={vi.fn()} />)
  expect(await screen.findByText('The sketch could not be compiled')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Try completing again/i }))

  expect(await screen.findByRole('heading', { name: /I think I’ve got you/ })).toBeInTheDocument()
  expect(complete).toHaveBeenCalledTimes(1)
})
