import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { LangProvider } from '../components/LangProvider'
import { BoutonPlus } from '../components/MobileNav'
import type { EntreePlus } from '../components/MobileNav'

const ENTREES: EntreePlus[] = [
  { id: 'voices', label: 'Voix', icon: 'volume' },
  { id: 'usage', label: 'Usage', icon: 'monitor' },
  { id: 'dedup', label: 'Doublons', icon: 'report' },
  { id: 'trash', label: 'Corbeille', icon: 'trash', badge: 3 },
]

function monter(courant = 'library', surChoisir = vi.fn()) {
  render(<LangProvider><BoutonPlus entrees={ENTREES} courant={courant} surChoisir={surChoisir} /></LangProvider>)
  return { surChoisir }
}

beforeEach(() => { localStorage.clear(); localStorage.setItem('memoglyph.lang', 'fr') })

describe('le bouton « Plus » du dock mobile', () => {
  it('reste fermé tant qu’on ne le touche pas ; un toucher ouvre les autres écrans', async () => {
    monter()
    const plus = screen.getByRole('button', { name: 'Plus' })
    expect(plus).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Voix' })).not.toBeInTheDocument()
    await userEvent.click(plus)
    expect(plus).toHaveAttribute('aria-expanded', 'true')
    const feuille = screen.getByRole('group', { name: /plus d.écrans/i })
    for (const nom of ['Voix', 'Usage', 'Doublons', /Corbeille/]) {
      expect(within(feuille).getByRole('button', { name: nom })).toBeInTheDocument()
    }
  })

  it('chaque écran secondaire est à DEUX touches au plus et referme la feuille', async () => {
    const { surChoisir } = monter()
    await userEvent.click(screen.getByRole('button', { name: 'Plus' }))
    await userEvent.click(screen.getByRole('button', { name: 'Doublons' }))
    expect(surChoisir).toHaveBeenCalledWith('dedup')
    expect(screen.getByRole('button', { name: 'Plus' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('Échap referme et rend le focus au bouton ; un toucher à côté referme aussi', async () => {
    monter()
    const plus = screen.getByRole('button', { name: 'Plus' })
    await userEvent.click(plus)
    await userEvent.keyboard('{Escape}')
    expect(plus).toHaveAttribute('aria-expanded', 'false')
    expect(plus).toHaveFocus()
    await userEvent.click(plus)
    await userEvent.click(document.body)
    expect(plus).toHaveAttribute('aria-expanded', 'false')
  })

  it('marque « Plus » comme courant quand l’écran affiché est l’un des siens', () => {
    monter('voices')
    expect(screen.getByRole('button', { name: 'Plus' })).toHaveClass('on')
  })

  it('le compteur de la corbeille est visible dans la feuille', async () => {
    monter()
    await userEvent.click(screen.getByRole('button', { name: 'Plus' }))
    expect(within(screen.getByRole('button', { name: /Corbeille/ })).getByText('3')).toBeInTheDocument()
  })
})
