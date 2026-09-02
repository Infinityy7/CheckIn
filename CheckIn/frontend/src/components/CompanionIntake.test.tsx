import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { CompanionIntake } from './CompanionIntake'
import { api, ApiError } from '../services/api'

const emptyThread = { turns: [], done: false }

afterEach(() => vi.restoreAllMocks())

it('walks the 4-question intake and reports the companion as profiled', async () => {
  vi.spyOn(api, 'profileChatTranscript').mockResolvedValue(emptyThread)
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
  expect(chat).toHaveBeenNthCalledWith(3, 'Street food always', 'Maya', expect.any(String))
  // every submitted answer carries its own turn key
  const keys = chat.mock.calls.slice(1).map((call) => call[2])
  expect(keys.every((key) => typeof key === 'string' && key.length > 0)).toBe(true)
  expect(new Set(keys).size).toBe(4)
})

it('offers a safe retry when the opener fails with a retryable error', async () => {
  vi.spyOn(api, 'profileChatTranscript').mockResolvedValue(emptyThread)
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

it('reuses the same turn key when an answer is retried', async () => {
  vi.spyOn(api, 'profileChatTranscript').mockResolvedValue(emptyThread)
  const chat = vi.spyOn(api, 'profileChat')
    .mockResolvedValueOnce({ reply: 'Q1: what pace suits Ravi?', done: false })
    .mockRejectedValueOnce(new ApiError('The server took too long to respond. It is safe to try again.', 0, 'REQUEST_TIMEOUT', undefined, true))
    .mockResolvedValueOnce({ reply: 'Q2: street food or safe picks?', done: false })
  render(<CompanionIntake name="Ravi" onProfiled={vi.fn()} onClose={vi.fn()} />)
  await screen.findByText(/Q1/)

  fireEvent.change(screen.getByLabelText(/answer as ravi/i), { target: { value: 'Slow and steady' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByText(/didn’t go through/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /try again/i }))
  expect(await screen.findByText(/Q2/)).toBeInTheDocument()

  expect(chat).toHaveBeenCalledTimes(3)
  expect(chat.mock.calls[1]).toEqual(['Slow and steady', 'Ravi', expect.any(String)])
  expect(chat.mock.calls[2]).toEqual(chat.mock.calls[1])
  expect(screen.getAllByText('Slow and steady')).toHaveLength(1)
})

it('restores a saved thread after a reload without re-sending the opener', async () => {
  vi.spyOn(api, 'profileChatTranscript').mockResolvedValue({
    turns: [
      { from: 'tavi', text: 'Q1: how does Maya pace a travel day?' },
      { from: 'user', text: 'Slow mornings' },
      { from: 'tavi', text: 'Q2: how adventurous is she with food?' },
    ],
    done: false,
  })
  const chat = vi.spyOn(api, 'profileChat')
  render(<CompanionIntake name="Maya" onProfiled={vi.fn()} onClose={vi.fn()} />)

  expect(await screen.findByText(/Q2/)).toBeInTheDocument()
  expect(screen.getByText('Slow mornings')).toBeInTheDocument()
  expect(chat).not.toHaveBeenCalled()
  await waitFor(() => expect(screen.getByLabelText(/answer as maya/i)).not.toBeDisabled())
})

it('fetches the missing reply when the saved thread ends on the traveler’s answer', async () => {
  vi.spyOn(api, 'profileChatTranscript').mockResolvedValue({
    turns: [{ from: 'tavi', text: 'Q1: pace?' }, { from: 'user', text: 'Slow mornings' }],
    done: false,
  })
  const chat = vi.spyOn(api, 'profileChat').mockResolvedValue({ reply: 'Q2: food?', done: false })
  render(<CompanionIntake name="Maya" onProfiled={vi.fn()} onClose={vi.fn()} />)

  expect(await screen.findByText('Q2: food?')).toBeInTheDocument()
  expect(chat).toHaveBeenCalledTimes(1)
  expect(chat).toHaveBeenCalledWith('', 'Maya')
  expect(screen.getAllByText('Slow mornings')).toHaveLength(1)
})

it('shows the finished state for a completed intake instead of starting over', async () => {
  vi.spyOn(api, 'profileChatTranscript').mockResolvedValue({
    turns: [{ from: 'tavi', text: 'Q1: pace?' }, { from: 'user', text: 'Slow' }],
    done: true,
  })
  const chat = vi.spyOn(api, 'profileChat')
  const onProfiled = vi.fn()
  render(<CompanionIntake name="Maya" onProfiled={onProfiled} onClose={vi.fn()} />)

  expect(await screen.findByText(/Maya is on the map/i)).toBeInTheDocument()
  expect(chat).not.toHaveBeenCalled()
  expect(onProfiled).not.toHaveBeenCalled()
})
