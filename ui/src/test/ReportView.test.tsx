import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { ReportView } from '../components/ReportView'

const REPORT = `---
type: report
genre: town_hall
---

# Compte-rendu

## Décision

- Le lancement est validé. [source 00:00:04]

<!-- memoglyph:evidence -->

## Sources et vérifications

### claim-001

- Le lancement est validé.
> [source 00:00:04] Le lancement est validé.

<!-- memoglyph:generated-notices -->
`

it('rend un CR compact, son genre et ses preuves repliees', () => {
  const onTimestamp = vi.fn()
  const { container } = render(
    <ReportView
      content={REPORT}
      genre="town_hall"
      onTimestamp={onTimestamp}
    />,
  )

  expect(screen.getByText('Réunion plénière interne')).toBeInTheDocument()
  const source = screen.getByRole('button', {
    name: 'Ouvrir la source à 00:00:04',
  })
  expect(source).toHaveTextContent('00:00:04')
  fireEvent.click(source)
  expect(onTimestamp).toHaveBeenCalledWith('00:00:04')

  const details = container.querySelector('details')
  expect(details).not.toBeNull()
  expect(details).not.toHaveAttribute('open')
  expect(screen.getByText('Sources et vérifications', { selector: 'summary' }))
    .toHaveTextContent('1')
})


it('conserve le rendu des anciens CR sans annexe', () => {
  render(<ReportView content="# Ancien compte-rendu" genre="internal_briefing" />)

  expect(screen.getByRole('heading', { name: 'Ancien compte-rendu' }))
    .toBeInTheDocument()
  expect(screen.getByText('Briefing interne')).toBeInTheDocument()
  expect(screen.queryByText('Sources et vérifications')).not.toBeInTheDocument()
})

it('ne replie l’annexe que derrière les marqueurs memoglyph', () => {
  const { container, unmount } = render(<ReportView content={REPORT} genre="town_hall" />)
  expect(container.querySelector('details')).not.toBeNull()
  expect(screen.getByText('Sources et vérifications', { selector: 'summary' }))
    .toHaveTextContent('1')
  unmount()
  // Le contrôle doit pouvoir échouer : un marqueur inconnu ne replie rien.
  const autre = render(
    <ReportView content={REPORT.replaceAll('<!-- memoglyph:', '<!-- autre:')} genre="town_hall" />,
  )
  expect(autre.container.querySelector('details')).toBeNull()
})

// Le H1 de tête répète le titre de la réunion, déjà dans l'en-tête. Chaque moteur
// l'écrit à sa façon : le générateur local (« Compte-rendu — », « Report — ») et
// l'écrivain commun des autres moteurs (`# <titre>`).
it.each([
  '# Report — ami-is1009a-reunion',
  '# Compte-rendu — ami-is1009a-reunion',
  '# ami-is1009a-reunion — compte-rendu',
])('retire de l’affichage le H1 de tête « %s »', (titre) => {
  const contenu = REPORT.replace('# Compte-rendu', titre)
  render(<ReportView content={contenu} genre="town_hall" />)

  expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Décision' })).toBeInTheDocument()
})

it('garde un H1 qui n’est pas le premier bloc', () => {
  const contenu = '## Décision\n\n- Validé.\n\n# Annexe\n\n- Détail.'
  render(<ReportView content={contenu} genre="generique" />)

  expect(screen.getByRole('heading', { level: 1, name: 'Annexe' })).toBeInTheDocument()
})

it.each([REPORT, '# Ancien compte-rendu\n\nTexte.'])(
  'affiche le modèle, lisible par un lecteur d’écran',
  (contenu) => {
    render(<ReportView content={contenu} genre="generique" model="mlx-community/Qwen3-4B" />)

    const puce = screen.getByText('mlx-community/Qwen3-4B').closest('.report-model')
    expect(puce).not.toBeNull()
    expect(puce).toHaveTextContent('Modèle · mlx-community/Qwen3-4B')
    expect(puce).toHaveAttribute('title', 'Modèle du compte-rendu')
    expect(puce).not.toHaveAttribute('aria-hidden')
    expect(puce).toBeVisible()
  },
)
