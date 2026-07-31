import { fireEvent, render, screen } from '@testing-library/react'
import { PostTripCheckIn } from './PostTripCheckIn'

it('stays out of the itinerary until the trip is eligible', () => {
  render(<PostTripCheckIn state={{ eligible: false }} onSubmit={vi.fn()} />)
  expect(screen.queryByText(/How did this trip feel/)).not.toBeInTheDocument()
})

it('submits one accessible 1–5 rating and shows the learned state', async () => {
  const submit = vi.fn().mockResolvedValue(undefined)
  const { rerender } = render(<PostTripCheckIn state={{ eligible: true }} onSubmit={submit} />)
  fireEvent.click(screen.getByRole('radio', { name: '5 out of 5' }))
  fireEvent.click(screen.getByRole('button', { name: /Save my rating/i }))
  expect(submit).toHaveBeenCalledWith(5)

  rerender(<PostTripCheckIn state={{ eligible: true, rating: 5, adjustments: [{ key: 'food', before: .2, after: .23, delta: .03 }] }} onSubmit={submit} />)
  expect(screen.getByText(/profile learned from this trip/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Change rating/i })).not.toBeInTheDocument()
})
