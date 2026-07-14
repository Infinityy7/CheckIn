import { ApiError } from './api'

describe('ApiError', () => {
  it('keeps the response status for UI recovery decisions', () => {
    const error = new ApiError('Sign in again', 401)
    expect(error.message).toBe('Sign in again')
    expect(error.status).toBe(401)
  })
})
