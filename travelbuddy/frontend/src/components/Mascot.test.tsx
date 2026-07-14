import { render, screen } from '@testing-library/react'
import { Mascot } from './Mascot'

describe('Mascot', () => {
  it.each(['neutral', 'greeting', 'thinking', 'excited', 'recommending', 'confused', 'celebrating', 'idle'] as const)('renders the %s state accessibly', (state) => {
    const { container } = render(<Mascot state={state} label={`Tavi ${state}`} />)
    expect(screen.getByRole('img', { name: `Tavi ${state}` })).toBeInTheDocument()
    expect(container.firstChild).toHaveClass(`mascot--${state}`)
  })
})
