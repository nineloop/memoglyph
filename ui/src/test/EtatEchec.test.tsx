/**
 * L'état d'échec partagé : une phrase de l'écran, une action « Réessayer », jamais le texte d'une exception.
 * Il s'annonce (`role="alert"`) sans prendre le focus ; seul un échec qui suit le « Réessayer » de la personne le reçoit.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { LangProvider } from '../components/LangProvider'
import { EtatEchec } from '../design/primitives'

function monter(langue: 'fr' | 'en', props: Partial<Parameters<typeof EtatEchec>[0]> = {}) {
  return render(
    <LangProvider initiale={langue}>
      <EtatEchec {...props}>Les doublons n’ont pas pu être chargés.</EtatEchec>
    </LangProvider>,
  )
}

describe('état d’échec partagé', () => {
  it('s’annonce comme une alerte et affiche la phrase fournie', () => {
    monter('fr')
    expect(screen.getByRole('alert')).toHaveTextContent('Les doublons n’ont pas pu être chargés.')
  })

  it('n’offre aucun bouton sans action de reprise', () => {
    monter('fr')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('propose « Réessayer » en français et « Try again » en anglais, et appelle la reprise', () => {
    const reprise = vi.fn()
    const fr = monter('fr', { onRetry: reprise })
    fireEvent.click(screen.getByRole('button', { name: 'Réessayer' }))
    expect(reprise).toHaveBeenCalledTimes(1)
    fr.unmount()

    const repriseEn = vi.fn()
    monter('en', { onRetry: repriseEn })
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(repriseEn).toHaveBeenCalledTimes(1)
  })

  it('ne montre rien d’autre que la phrase et le bouton', () => {
    const { container } = monter('fr', { onRetry: vi.fn() })
    expect(container.textContent).toBe('Les doublons n’ont pas pu être chargés.Réessayer')
  })

  it('ne prend pas le focus en apparaissant : un échec de relève ne change pas le contexte de la personne', () => {
    const ailleurs = document.createElement('button')
    document.body.appendChild(ailleurs)
    ailleurs.focus()
    monter('fr', { onRetry: vi.fn() })
    expect(document.activeElement).toBe(ailleurs)
    document.body.removeChild(ailleurs)
  })

  it('donne le focus au bouton quand l’échec suit le « Réessayer » de la personne', () => {
    monter('fr', { onRetry: vi.fn(), focusRetry: true })
    expect(screen.getByRole('button', { name: 'Réessayer' })).toHaveFocus()
  })
})
