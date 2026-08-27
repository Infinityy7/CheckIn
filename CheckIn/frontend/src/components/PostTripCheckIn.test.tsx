import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  await waitFor(() => expect(submit).toHaveBeenCalledWith(5))
  await waitFor(() => expect(screen.getByRole('button', { name: /Save my rating/i })).toBeEnabled())

  rerender(<PostTripCheckIn state={{ eligible: true, rating: 5, adjustments: [{ key: 'food', before: .2, after: .23, delta: .03 }] }} onSubmit={submit} />)
  expect(screen.getByText(/profile learned from this trip/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Change rating/i })).not.toBeInTheDocument()
})

it('lists what Tavi learned with signed deltas after submission', () => {
  render(<PostTripCheckIn state={{ eligible: true, rating: 4, adjustments: [
    { key: 'food', before: .2, after: .23, delta: .03 },
    { key: 'pace', before: .5, after: .45, delta: -.05 },
  ] }} onSubmit={vi.fn()} />)
  expect(screen.getByText('What Tavi learned')).toBeInTheDocument()
  expect(screen.getByText('food')).toBeInTheDocument()
  expect(screen.getByText('pace')).toBeInTheDocument()
  expect(screen.getByText('+0.03')).toHaveClass('chip--ok')
  expect(screen.getByText('-0.05')).toHaveClass('chip--warn')
})

it('skips the learned list when no adjustments came back', () => {
  render(<PostTripCheckIn state={{ eligible: true, rating: 3, adjustments: [] }} onSubmit={vi.fn()} />)
  expect(screen.getByText(/You rated it 3 out of 5\./)).toBeInTheDocument()
  expect(screen.queryByText('What Tavi learned')).not.toBeInTheDocument()
})
