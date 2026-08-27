import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { CompanionIntake } from './CompanionIntake'
import { api, ApiError } from '../services/api'

afterEach(() => vi.restoreAllMocks())

it('walks the 4-question intake and reports the companion as profiled', async () => {
  const chat = vi.spyOn(api, 'profileChat')
    .mockResolvedValueOnce({ reply: 'Q1: how does Maya pace a travel day?', done: false })
    .mockResolvedValueOnce({ reply: 'Q2: how adventurous is she with food?', done: false })
    .mockResolvedValueOnce({ reply: 'Q3: strict budget or flexible?', done: false })
    .mockResolvedValueOnce({ reply: 'Q4: early starts or late nights?', done: false })
    .mockResolvedValueOnce({ reply: 'All set — the sketch is saved.', done: true })
  const onProfiled = vi.fn()
  render(<CompanionIntake name="Maya" onProfiled={onProfiled} onClose={vi.fn()} />)

  expect(await screen.findByText(/Q1/)).toBeInTheDocument()
  expect(chat).toHaveBeenNthCalledWith(1, '', 'Maya')

  const answers = ['Slow mornings, one big thing a day', 'Street food always', 'Mid-range with splurges', 'Early bird']
  for (const [index, answer] of answers.entries()) {
    fireEvent.change(screen.getByLabelText(/answer as maya/i), { target: { value: answer } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    if (index < answers.length - 1) expect(await screen.findByText(new RegExp(`Q${index + 2}`))).toBeInTheDocument()
  }

  expect(await screen.findByText(/Maya is on the map/i)).toBeInTheDocument()
  await waitFor(() => expect(onProfiled).toHaveBeenCalledWith('Maya'))
  expect(chat).toHaveBeenCalledTimes(5)
  expect(chat).toHaveBeenNthCalledWith(3, 'Street food always', 'Maya')
})

it('offers a safe retry when a turn fails with a retryable error', async () => {
  vi.spyOn(api, 'profileChat')
    .mockRejectedValueOnce(new ApiError('The server took too long to respond. It is safe to try again.', 0, 'REQUEST_TIMEOUT', undefined, true))
    .mockResolvedValueOnce({ reply: 'Q1: what pace suits Ravi?', done: false })
  const onProfiled = vi.fn()
  render(<CompanionIntake name="Ravi" onProfiled={onProfiled} onClose={vi.fn()} />)

  expect(await screen.findByText(/took too long/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /try again/i }))
  expect(await screen.findByText(/Q1/)).toBeInTheDocument()
  expect(onProfiled).not.toHaveBeenCalled()
})
