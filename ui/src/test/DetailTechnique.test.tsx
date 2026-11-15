import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'

import { DetailTechnique } from '../components/DetailTechnique'
import { LangProvider } from '../components/LangProvider'

const DETAIL = 'HTTP 500: {"detail":"detail-brut-du-serveur"}'

it('pour un administrateur : le détail est là, sous un titre traduit, et replié', () => {
  render(<LangProvider initiale="fr"><DetailTechnique detail={DETAIL} admin /></LangProvider>)
  const bloc = document.querySelector('details') as HTMLDetailsElement
  expect(bloc).not.toBeNull()
  expect(bloc.open).toBe(false)
  expect(screen.getByText('Détail technique')).toBeInTheDocument()
  expect(bloc).toHaveTextContent('detail-brut-du-serveur')
})

it('pour un administrateur, en anglais : le titre est traduit', () => {
  render(<LangProvider initiale="en"><DetailTechnique detail={DETAIL} admin /></LangProvider>)
  expect(screen.getByText('Technical detail')).toBeInTheDocument()
})

it('pour un compte qui n’est pas administrateur : le détail n’est PAS dans le DOM (un bloc replié le laisserait dans le DOM)', () => {
  const { container } = render(<LangProvider initiale="fr"><DetailTechnique detail={DETAIL} admin={false} /></LangProvider>)
  expect(container.querySelector('details')).toBeNull()
  expect(screen.queryByText(/detail-brut-du-serveur/)).toBeNull()
  expect(document.body.textContent).not.toMatch(/HTTP|detail-brut/)
})

it('sans détail, rien ne s’affiche, même pour un administrateur', () => {
  const { container } = render(<LangProvider initiale="fr"><DetailTechnique detail="" admin /></LangProvider>)
  expect(container.querySelector('details')).toBeNull()
})
