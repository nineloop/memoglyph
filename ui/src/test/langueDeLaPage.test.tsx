import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it } from 'vitest'

import { LangProvider, LangSelector } from '../components/LangProvider'

// Le titre diffère d'une langue à l'autre : `index.html` ne porte que « Memoglyph », un titre identique
// dans les deux langues rendrait ces tests vides.
const TITRE = { fr: 'Memoglyph : vos réunions', en: 'Memoglyph: your meetings' }

beforeEach(() => {
  // État de départ qui n'est celui d'aucune des deux langues attendues : seul le code peut le changer.
  document.documentElement.lang = 'xx'
  document.title = 'Memoglyph'
})


it('la page porte la langue de départ et son titre : français', () => {
  render(<LangProvider initiale="fr"><span /></LangProvider>)
  expect(document.documentElement.lang).toBe('fr')
  expect(document.title).toBe(TITRE.fr)
})

it('la page porte la langue de départ et son titre : anglais', () => {
  render(<LangProvider initiale="en"><span /></LangProvider>)
  expect(document.documentElement.lang).toBe('en')
  expect(document.title).toBe(TITRE.en)
})

it('changer la langue change aussi la langue de la page et son titre', async () => {
  const user = userEvent.setup()
  render(<LangProvider initiale="fr"><LangSelector /></LangProvider>)
  expect(document.documentElement.lang).toBe('fr')

  await user.selectOptions(screen.getByRole('combobox'), 'en')
  expect(document.documentElement.lang).toBe('en')
  expect(document.title).toBe(TITRE.en)

  await user.selectOptions(screen.getByRole('combobox'), 'fr')
  expect(document.documentElement.lang).toBe('fr')
  expect(document.title).toBe(TITRE.fr)
})
