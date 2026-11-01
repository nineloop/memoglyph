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
