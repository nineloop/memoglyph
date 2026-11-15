import { screen, waitFor, within } from '@testing-library/react'
import { expect } from 'vitest'

import { ConfirmationProvider } from '../design/Confirmation'

/** À passer à `render(ui, { wrapper: avecConfirmation })` : les composants qui demandent une confirmation en ont besoin. */
export const avecConfirmation = ConfirmationProvider

type Utilisateur = { click(element: Element): Promise<void> }

const estLaFenetre = (dialogue: HTMLElement) => dialogue.getAttribute('aria-modal') === 'true'

/** La fenêtre de confirmation ouverte (un dialogue modal ; le panneau des Traitements, lui, n'est pas modal). */
export async function fenetreDeConfirmation(): Promise<HTMLElement> {
  return waitFor(() => {
    const fenetre = screen.queryAllByRole('dialog').find(estLaFenetre)
    if (!fenetre) throw new Error('aucune fenêtre de confirmation ouverte')
    return fenetre
  })
}

/** Aucune fenêtre de confirmation n'est ouverte. */
export function aucuneFenetreDeConfirmation(): void {
  expect(screen.queryAllByRole('dialog').find(estLaFenetre)).toBeUndefined()
}

/** Clique sur le bouton qui confirme l'action (le dernier de la fenêtre). */
export async function confirmer(utilisateur: Utilisateur): Promise<void> {
  const boutons = within(await fenetreDeConfirmation()).getAllByRole('button')
  await utilisateur.click(boutons[boutons.length - 1])
}

/** Clique sur « Annuler » (le premier bouton de la fenêtre). */
export async function refuser(utilisateur: Utilisateur): Promise<void> {
  await utilisateur.click(within(await fenetreDeConfirmation()).getAllByRole('button')[0])
}

/** La phrase de la fenêtre (le texte qui était celui de la boîte native). */
export async function phraseDeLaFenetre(): Promise<string> {
  return within(await fenetreDeConfirmation()).getByText((_t, el) => el?.tagName === 'P').textContent ?? ''
}
