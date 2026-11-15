import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'

import { MarkdownView } from '../components/MarkdownView'

it('rend les blocs usuels sans injecter de HTML ni de lien dangereux', () => {
  render(<MarkdownView content={'# Titre\n\n- **Action** sûre\n\n[lien](javascript:alert(1))\n\n`code`'} />)

  expect(screen.getByRole('heading', { name: 'Titre' })).toBeInTheDocument()
  expect(screen.getByRole('listitem')).toHaveTextContent('Action sûre')
  expect(screen.queryByRole('link')).not.toBeInTheDocument()
  expect(screen.getByText('code').tagName).toBe('CODE')
})

it('masque le séparateur machine des réserves', () => {
  render(<MarkdownView content={'Avant\n\n<!-- memoglyph:generated-notices -->\n\nAprès'} />)

  expect(screen.getByText('Avant')).toBeInTheDocument()
  expect(screen.getByText('Après')).toBeInTheDocument()
  expect(screen.queryByText(/generated-notices/)).not.toBeInTheDocument()
})

it('rend l’italique du compte rendu sans ses astérisques, et le gras reste du gras', () => {
  render(<MarkdownView content={'*Interpretation grounded in the cited units, not verbatim-verified.*\n\nUn **point** clé, et 2 * 3 * 4.'} />)

  const italique = screen.getByText('Interpretation grounded in the cited units, not verbatim-verified.')
  expect(italique.tagName).toBe('EM')
  expect(screen.getByText('point').tagName).toBe('STRONG')
  expect(document.body.textContent).not.toMatch(/\*Interpretation|verified\.\*/)
  expect(document.body.textContent).toContain('2 * 3 * 4')
})


it('un titre de niveau 1 du document est rendu en h2 : la page garde son seul h1', () => {
  render(<MarkdownView content={'# Annexe\n\n## Détail'} />)

  expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 2, name: 'Annexe' })).toHaveClass('md-h1')   // il garde l'aspect d'un titre de niveau 1
  expect(screen.getByRole('heading', { level: 2, name: 'Détail' })).not.toHaveClass('md-h1')
})
