import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { ThemeProvider } from '../design/theme'
import { ThemeToggle } from '../design/primitives'

const monter = () => render(<ThemeProvider><ThemeToggle /></ThemeProvider>)

describe('thème de l\'interface', () => {
  it('démarre en « système » et pose data-theme=light quand matchMedia est absent (jsdom)', () => {
    monter()
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(screen.getByTitle('Thème système')).toHaveAttribute('aria-pressed', 'true')
  })

  it('bascule en sombre : met à jour data-theme et persiste le choix', () => {
    monter()
    fireEvent.click(screen.getByTitle('Thème sombre'))
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(localStorage.getItem('memoglyph.theme')).toBe('dark')
    expect(screen.getByTitle('Thème sombre')).toHaveAttribute('aria-pressed', 'true')
  })

  it('revient au clair sur demande explicite', () => {
    monter()
    fireEvent.click(screen.getByTitle('Thème clair'))
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(localStorage.getItem('memoglyph.theme')).toBe('light')
  })
})
