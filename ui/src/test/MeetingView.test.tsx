import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import type { LibraryItem } from '../api'
import type { Reunion } from '../lib/meetings'
import { archiveTranscript, getArchivePlan, getTranscript, makeReport } from '../api'
import { MeetingView } from '../components/MeetingView'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getTranscript: vi.fn(),
    makeReport: vi.fn(),
    archiveTranscript: vi.fn(),
    getArchivePlan: vi.fn(),
  }
})

function item(name: string, modified: number, type: string | null = null): LibraryItem {
  return {
    name, id: `${name}-id`, meeting_id: 'meeting-id', project_id: 'project-id',
    title: 'weekly-team-sync', date: '2026-07-21', duration: '00:42:00', language: 'fr',
    engine: 'whisper', source: 'Weekly Team Sync.mp4', project: 'Non classé',
    diarization: type !== 'report', profiled: type !== 'report', n_speakers: type === 'report' ? null : 4,
    type, source_transcript: type === 'report' ? 'transcript-a.md' : null,
    source_document_id: type === 'report' ? 'transcript-a.md-id' : null,
    warnings: name === 'report-a.md' ? 1 : 0, size: 100, modified,
  }
}

const transcriptA = item('transcript-a.md', Date.UTC(2026, 6, 21, 8, 0, 5) / 1000)
const transcriptB = item('transcript-b.md', Date.UTC(2026, 6, 21, 8, 0, 42) / 1000)
const reportA = item('report-a.md', Date.UTC(2026, 6, 21, 10, 0, 5) / 1000, 'report')
const reportB = item('report-b.md', Date.UTC(2026, 6, 21, 10, 0, 42) / 1000, 'report')

const reunion: Reunion = {
  cle: 'meeting-id', source: 'Weekly Team Sync.mp4', titre: 'weekly-team-sync', date: '2026-07-21',
  duree: '00:42:00', langue: 'fr', projet: 'Non classé', nSpeakers: 4, avertissements: 1,
  transcripts: [transcriptA, transcriptB], comptesRendus: [reportA, reportB], retenu: transcriptA,
}

beforeEach(() => {
  vi.mocked(makeReport).mockResolvedValue({
    name: 'report-a.md', content: '# Nouveau compte-rendu', genre: 'town_hall',
  })
  vi.mocked(getTranscript).mockImplementation(async (name) => {
    if (name.startsWith('report')) {
      return {
        name,
        meta: name === 'report-a.md'
          ? { type: 'report', genre: 'town_hall', unsupported_numbers: ['42'] }
          : { type: 'report', genre: 'internal_briefing' },
        body: '# Synthèse\n\n## Décisions\n\n- **Lancer** le pilote\n- Préparer le bilan',
        content: '# Synthèse',
      }
    }
    return { name, meta: {}, body: 'Transcript', content: '[00:00] Speaker 1 : Bonjour', has_audio: true }
  })
})

it('ajoute immédiatement un compte-rendu généré à la réunion ouverte', async () => {
  const user = userEvent.setup()
  const changed = vi.fn()
  const withoutReport = { ...reunion, comptesRendus: [], avertissements: 0 }
  const { rerender } = render(
    <MeetingView reunion={withoutReport} onBack={vi.fn()} onChanged={changed} />,
  )
  await screen.findByText('Transcript')

  await user.click(screen.getByRole('button', { name: /Compte-rendu/ }))

  expect(await screen.findByRole('heading', { name: 'Nouveau compte-rendu' })).toBeInTheDocument()
  expect(changed).toHaveBeenCalledOnce()

  rerender(
    <MeetingView
      reunion={{ ...withoutReport, comptesRendus: [reportA] }}
      onBack={vi.fn()}
      onChanged={changed}
    />,
  )
  await user.click(screen.getByRole('tab', { name: /Compte-rendu 1/ }))

  expect(await screen.findByRole('heading', { name: 'Décisions', level: 2 })).toBeInTheDocument()
})

it('rend le compte-rendu en Markdown et sépare les quatre espaces', async () => {
  render(<MeetingView reunion={reunion} onBack={vi.fn()} onVoirDoublons={vi.fn()} />)

  expect(await screen.findByRole('heading', { name: 'Décisions', level: 2 })).toBeInTheDocument()
  // Le H1 de tête répète le titre de la réunion : il n'est pas affiché.
  expect(screen.queryByRole('heading', { name: 'Synthèse' })).not.toBeInTheDocument()
  expect(screen.getByText('Lancer').closest('li')).toHaveTextContent('Lancer le pilote')
  expect(screen.getByText('Réunion plénière interne')).toBeInTheDocument()
  expect(screen.queryByText('# Synthèse')).not.toBeInTheDocument()

  for (const tab of ['Compte-rendu', 'Transcript', 'Versions', 'Informations']) {
    expect(screen.getByRole('tab', { name: new RegExp(tab) })).toBeInTheDocument()
  }
  expect(screen.getByLabelText('Actions de la réunion')).toBeInTheDocument()
})

it('distingue les versions par date et heure et ouvre le document choisi', async () => {
  const user = userEvent.setup()
  render(<MeetingView reunion={reunion} onBack={vi.fn()} />)
  await screen.findByRole('heading', { name: 'Décisions' })

  const reportChoices = screen.getAllByRole('button', { name: /Afficher le compte-rendu \d du/ })
  expect(reportChoices).toHaveLength(2)
  expect(reportChoices[0]).not.toHaveAccessibleName(reportChoices[1].getAttribute('aria-label') || '')

  await user.click(screen.getByRole('tab', { name: /Versions/ }))
  const panel = screen.getByRole('tabpanel', { name: 'Versions' })
  const timestamps = within(panel).getAllByText(/21\/07\/2026.*\d{2}:\d{2}:\d{2}/)
  expect(new Set(timestamps.map((node) => node.textContent)).size).toBeGreaterThan(1)

  await user.click(within(panel).getAllByRole('button', { name: 'Afficher' })[0])
  await waitFor(() => expect(getTranscript).toHaveBeenCalledWith('transcript-b.md', 'project-id'))
  expect(screen.getByRole('tabpanel', { name: 'Transcript' })).toBeVisible()
})

it('rattache chaque réserve au document et à son heure exacts', async () => {
  const user = userEvent.setup()
  render(<MeetingView reunion={reunion} onBack={vi.fn()} />)
  await screen.findByRole('heading', { name: 'Décisions' })

  await user.click(screen.getByRole('button', { name: /1 réserve de qualité/ }))
  expect(await screen.findByText(/Compte-rendu · 21\/07\/2026.*\d{2}:\d{2}:\d{2} · report-a\.md/)).toBeInTheDocument()
  expect(screen.getByText(/1 chiffre non retrouvé/)).toBeInTheDocument()
})

it('permet d’archiver unitairement un compte-rendu ou un transcript', async () => {
  const user = userEvent.setup()
  const changed = vi.fn()
  vi.mocked(getArchivePlan).mockResolvedValue({ requested: 'report-a.md', documents: [{ name: 'report-a.md', title: 'report-a.md', type: 'report', document_id: 'doc-1' }] })
  vi.mocked(archiveTranscript).mockResolvedValue({ archived: 'report-a.md', documents: ['report-a.md'] })
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

  render(<MeetingView reunion={reunion} onBack={vi.fn()} onChanged={changed} />)
  await screen.findByRole('heading', { name: 'Décisions' })

  await user.click(screen.getByRole('button', { name: 'Archiver ce compte-rendu' }))
  expect(confirmSpy).toHaveBeenCalledOnce()
  expect(archiveTranscript).toHaveBeenCalledWith('report-a.md', 'project-id')
  expect(changed).toHaveBeenCalledOnce()

  confirmSpy.mockRestore()
})

/**
 * L'onglet de réunion vit dans l'URL (voir `lib/urlState.ts`).
 *
 * Défaut visible à l'écran, qu'aucun test ne voyait : le bouton retour
 * changeait l'URL et laissait l'écran sur l'onglet précédent. `initialTab`
 * n'était lu qu'au montage, le même piège que pour la réunion elle-même.
 */
it('ouvre sur l’onglet imposé par l’URL', () => {
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="versions" />)
  const actif = document.querySelector('[role="tab"][aria-selected="true"]')
  expect(actif?.textContent).toMatch(/Versions/)
})

it('SUIT un changement d’onglet imposé après le montage', () => {
  // C'est ce que fait le bouton retour : le parent relit l'URL et impose un
  // onglet à un composant déjà monté.
  const { rerender } = render(
    <MeetingView reunion={reunion} onBack={vi.fn()} initialTab="versions" />)
  expect(document.querySelector('[role="tab"][aria-selected="true"]')?.textContent)
    .toMatch(/Versions/)

  rerender(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="info" />)
  expect(document.querySelector('[role="tab"][aria-selected="true"]')?.textContent)
    .toMatch(/Informations/)
})

it('remonte le changement d’onglet au parent', () => {
  // Sans cet écho, l'URL ne saurait pas que l'utilisateur a changé d'onglet.
  const change = vi.fn()
  render(<MeetingView reunion={reunion} onBack={vi.fn()} onTabChange={change} />)
  const onglets = Array.from(document.querySelectorAll('[role="tab"]'))
  const versions = onglets.find((t) => /Versions/.test(t.textContent ?? ''))
  versions?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  expect(change).toHaveBeenCalledWith('versions')
})

it('IGNORE un onglet inconnu et retombe sur le contenu disponible', () => {
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="nimporte" />)
  const actif = document.querySelector('[role="tab"][aria-selected="true"]')
  expect(actif?.textContent).toMatch(/Compte-rendu|Transcript/)
})

/**
 * L'en-tête se compacte en lecture.
 *
 * Le point qui compte n'est PAS la classe CSS : c'est qu'aucun fait ne
 * disparaisse au passage. Compacter en perdant la langue ou le nombre de
 * locuteurs serait une régression déguisée en gain de place.
 */
const FAITS_ATTENDUS = ['21/07/2026', '00:42:00', 'FR', '4 locuteurs', 'Non classé']

it('compacte l’en-tête en lecture SANS perdre un seul fait', () => {
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="transcript" />)

  const entete = document.querySelector('.meeting-hero')
  expect(entete).toHaveClass('compact')
  // La grille libellée cède la place à une ligne — sinon on aurait les deux.
  expect(document.querySelector('.meeting-hero-facts')).toBeNull()

  const ligne = document.querySelector('.meeting-hero-line')
  for (const fait of FAITS_ATTENDUS) expect(ligne).toHaveTextContent(fait)
})

it('garde l’en-tête PLEIN hors lecture, avec les mêmes faits libellés', async () => {
  const user = userEvent.setup()
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="info" />)

  expect(document.querySelector('.meeting-hero')).not.toHaveClass('compact')
  expect(document.querySelector('.meeting-hero-line')).toBeNull()

  const grille = screen.getByLabelText('Informations principales')
  for (const libelle of ['Date', 'Durée', 'Langue', 'Locuteurs', 'Projet']) {
    expect(within(grille).getByText(libelle)).toBeInTheDocument()
  }
  expect(within(grille).getByText('4')).toBeInTheDocument()

  // Et le basculement se fait bien À CHAUD, pas seulement au montage.
  await user.click(screen.getByRole('tab', { name: /Transcript/ }))
  expect(document.querySelector('.meeting-hero')).toHaveClass('compact')
})

it('dit « inconnu » plutôt que « — » quand un fait sort de sa colonne', () => {
  // Un tiret cadratin se lit dans une grille libellée ; seul dans une phrase,
  // il ne dit rien. C'est pourquoi chaque fait porte une forme autonome.
  const creuse: Reunion = {
    ...reunion, date: '', duree: null, langue: null, nSpeakers: null, projet: '',
  }
  render(<MeetingView reunion={creuse} onBack={vi.fn()} initialTab="transcript" />)

  const ligne = document.querySelector('.meeting-hero-line')
  expect(ligne).toHaveTextContent(
    'Date inconnue · Durée inconnue · Langue inconnue · Locuteurs inconnus · Non classé')
})

it('un locuteur reste au SINGULIER', () => {
  render(
    <MeetingView reunion={{ ...reunion, nSpeakers: 1 }} onBack={vi.fn()} initialTab="transcript" />)
  expect(document.querySelector('.meeting-hero-line')).toHaveTextContent('1 locuteur ·')
})

it('le bandeau de réserves SURVIT au mode compact', () => {
  // Gagner de la place en masquant un avertissement de qualité serait le pire
  // des gains : l'utilisateur lirait un document sans savoir ce qu'on lui doit.
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="transcript" />)
  expect(document.querySelector('.meeting-hero')).toHaveClass('compact')
  expect(screen.getByRole('button', { name: /1 réserve de qualité/ })).toBeInTheDocument()
})
