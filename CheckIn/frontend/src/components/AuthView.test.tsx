import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AuthView } from './AuthView'
import { api, ApiError } from '../services/api'

// ThemeToggle reads the document theme; pin it so jsdom never needs matchMedia.
beforeEach(() => { document.documentElement.dataset.theme = 'light' })
afterEach(() => { vi.restoreAllMocks(); delete document.documentElement.dataset.theme })

function fillRegistration() {
  fireEvent.change(screen.getByLabelText(/Full name/i), { target: { value: 'Sam Fernandes' } })
  fireEvent.change(screen.getByLabelText(/Username/i), { target: { value: 'SamTravels' } })
  fireEvent.change(screen.getByLabelText(/Email/i), { target: { value: 'sam@example.com' } })
  fireEvent.change(screen.getByLabelText(/Phone/i), { target: { value: '+91 98765 43210' } })
  fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: 'wanderlust' } })
}

it('registers with the full payload, lowercasing the username, then hands off', async () => {
  const register = vi.spyOn(api, 'register').mockResolvedValue(undefined)
  const onAuthenticated = vi.fn()
  render(<AuthView onAuthenticated={onAuthenticated} />)

  fillRegistration()
  fireEvent.click(screen.getByRole('button', { name: /Meet Tavi/i }))

  await waitFor(() => expect(register).toHaveBeenCalledWith({
    email: 'sam@example.com',
    password: 'wanderlust',
    username: 'samtravels',
    name: 'Sam Fernandes',
    phone: '+91 98765 43210',
  }))
  await waitFor(() => expect(onAuthenticated).toHaveBeenCalled())
})

it('shows the username as Available when the lookup returns 404', async () => {
  const lookup = vi.spyOn(api, 'lookupUser').mockRejectedValue(new ApiError('No such traveller.', 404, 'NOT_FOUND'))
  render(<AuthView onAuthenticated={vi.fn()} />)

  const field = screen.getByLabelText(/Username/i)
  fireEvent.change(field, { target: { value: 'newcomer' } })
  fireEvent.blur(field)

  expect(await screen.findByText('Available')).toBeInTheDocument()
  expect(lookup).toHaveBeenCalledWith('newcomer')
})

it('shows the username as Taken when the lookup succeeds', async () => {
  vi.spyOn(api, 'lookupUser').mockResolvedValue({ username: 'wanderer', name: 'Someone Else', intake_complete: true, link_status: 'accepted' })
  render(<AuthView onAuthenticated={vi.fn()} />)

  const field = screen.getByLabelText(/Username/i)
  fireEvent.change(field, { target: { value: 'wanderer' } })
  fireEvent.blur(field)

  expect(await screen.findByText('Taken')).toBeInTheDocument()
})

it('logs in with the identifier and password', async () => {
  const login = vi.spyOn(api, 'login').mockResolvedValue(undefined)
  const onAuthenticated = vi.fn()
  render(<AuthView onAuthenticated={onAuthenticated} />)

  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
  fireEvent.change(screen.getByLabelText(/Email or username/i), { target: { value: 'samtravels' } })
  fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: 'wanderlust' } })
  fireEvent.click(screen.getByRole('button', { name: /Open my workspace/i }))

  await waitFor(() => expect(login).toHaveBeenCalledWith('samtravels', 'wanderlust'))
  await waitFor(() => expect(onAuthenticated).toHaveBeenCalled())
})

it('surfaces the ApiError message when registration fails', async () => {
  vi.spyOn(api, 'register').mockRejectedValue(new ApiError('That email is already registered.', 400, 'EMAIL_TAKEN'))
  const onAuthenticated = vi.fn()
  render(<AuthView onAuthenticated={onAuthenticated} />)

  fillRegistration()
  fireEvent.click(screen.getByRole('button', { name: /Meet Tavi/i }))

  expect(await screen.findByRole('alert')).toHaveTextContent('That email is already registered.')
  expect(onAuthenticated).not.toHaveBeenCalled()
})

it('keeps the entered email when switching modes', () => {
  render(<AuthView onAuthenticated={vi.fn()} />)

  fireEvent.change(screen.getByLabelText(/Email/i), { target: { value: 'sam@example.com' } })
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
  fireEvent.click(screen.getByRole('button', { name: 'Create account' }))

  expect(screen.getByLabelText(/Email/i)).toHaveValue('sam@example.com')
})
