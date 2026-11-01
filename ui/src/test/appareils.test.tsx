import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import type { JetonAppareil, LibraryItem } from '../api'
import { creerJeton, getEtatAuth, getTranscript, listerJetons, retirerJeton } from '../api'
import { AppareilsPanel, BoutonAppareils } from '../components/AppareilsPanel'
import { AuthGate } from '../components/AuthGate'
import { LangProvider } from '../components/LangProvider'
import { MeetingView } from '../components/MeetingView'
import type { Reunion } from '../lib/meetings'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getEtatAuth: vi.fn(), listerJetons: vi.fn(), creerJeton: vi.fn(), retirerJeton: vi.fn(),
    getTranscript: vi.fn(),
  }
})

const MAC: JetonAppareil = { id: 'J-1', nom: 'MacBook', cree_le: '2026-10-21T10:00:00+00:00',
                             dernier_usage: null }
// Assemblé à l'exécution : le contrôle d'export ne doit pas y lire un jeton.
const SECRET = 'mgj_' + 'secret-de-test-affiche-une-fois'

beforeEach(() => {
  vi.mocked(listerJetons).mockReset().mockResolvedValue([MAC])
  vi.mocked(creerJeton).mockReset()
  vi.mocked(retirerJeton).mockReset().mockResolvedValue(undefined)
})

// ---------- (1) Créer, voir, retirer un jeton d'appareil ----------

it('le bouton « Appareils » existe pour tout compte connecté, et Échap ferme', async () => {
  const user = userEvent.setup()
  vi.mocked(getEtatAuth).mockResolvedValue({
    installation_requise: false, connecte: true, utilisateur: { identifiant: 'alice', admin: false },
  })
  render(<LangProvider><AuthGate><BoutonAppareils /></AuthGate></LangProvider>)

  await user.click(await screen.findByRole('button', { name: 'Appareils' }))
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(await screen.findByText('MacBook')).toBeInTheDocument()

  await user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('liste les jetons sans jamais leur secret, « Jamais » s’ils n’ont pas servi', async () => {
  render(<LangProvider><AppareilsPanel /></LangProvider>)

  const ligne = (await screen.findByText('MacBook')).closest('tr')!
  expect(within(ligne).getByText('Jamais')).toBeInTheDocument()
  expect(screen.queryByLabelText('Jeton d’appareil')).not.toBeInTheDocument()
})

it('montre le secret une seule fois, à la création', async () => {
  const user = userEvent.setup()
  const neuf = { ...MAC, id: 'J-2', nom: 'Mac du bureau' }
  vi.mocked(creerJeton).mockResolvedValue({ jeton: neuf, secret: SECRET })
  const { unmount } = render(<LangProvider><AppareilsPanel /></LangProvider>)
  await screen.findByText('MacBook')
  vi.mocked(listerJetons).mockResolvedValue([MAC, neuf])

  await user.type(screen.getByLabelText('Nom de l’appareil'), 'Mac du bureau')
  await user.click(screen.getByRole('button', { name: 'Créer le jeton' }))

  expect(creerJeton).toHaveBeenCalledWith('Mac du bureau')
  expect(await screen.findByLabelText('Jeton d’appareil')).toHaveValue(SECRET)
  expect(screen.getByText(/il ne sera plus affiché/)).toBeInTheDocument()
  expect(await screen.findByRole('cell', { name: 'Mac du bureau' })).toBeInTheDocument()

  // Rouvert, le panneau relit la liste : le secret n'y est plus.
  unmount()
  render(<LangProvider><AppareilsPanel /></LangProvider>)
  await screen.findByRole('cell', { name: 'Mac du bureau' })
  expect(screen.queryByLabelText('Jeton d’appareil')).not.toBeInTheDocument()
  expect(document.body).not.toHaveTextContent(SECRET)
})

it('retire un jeton et affiche le refus du serveur tel quel', async () => {
  const user = userEvent.setup()
  render(<LangProvider><AppareilsPanel /></LangProvider>)
  const ligne = (await screen.findByText('MacBook')).closest('tr')!
  vi.mocked(listerJetons).mockResolvedValue([])

  await user.click(within(ligne).getByRole('button', { name: 'Retirer' }))

  expect(retirerJeton).toHaveBeenCalledWith('J-1')
  expect(await screen.findByText('Jeton « MacBook » retiré.')).toBeInTheDocument()
  expect(await screen.findByText('Aucun jeton pour l’instant.')).toBeInTheDocument()

  vi.mocked(creerJeton).mockRejectedValue(new Error('Nom d’appareil invalide'))
  await user.type(screen.getByLabelText('Nom de l’appareil'), 'x')
  await user.click(screen.getByRole('button', { name: 'Créer le jeton' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Nom d’appareil invalide')
})

// ---------- (3) Provenance d'une capture dans l'onglet Information ----------

function item(name: string): LibraryItem {
  return {
    name, id: `${name}-id`, meeting_id: 'meeting-id', project_id: 'project-id', title: 'visio',
    date: '2026-10-21', duration: '00:42:00', language: 'fr', engine: 'whisper', source: 'visio.wav',
    project: 'Non classé', diarization: true, profiled: false, n_speakers: 2, type: null,
    source_transcript: null, source_document_id: null, warnings: 0, size: 100, modified: 1,
  }
}

const reunion: Reunion = {
  cle: 'meeting-id', source: 'visio.wav', titre: 'visio', date: '2026-10-21', duree: '00:42:00',
  langue: 'fr', projet: 'Non classé', nSpeakers: 2, avertissements: 0,
  transcripts: [item('visio.md')], comptesRendus: [], retenu: item('visio.md'),
}

it('l’onglet Information montre l’application et le mode de la capture', async () => {
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'visio.md', meta: { capture: { application: 'zoom.us', mode: 'visio' } },
    body: 'T', content: 'T', has_audio: true,
  })
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="info" />)

  const panneau = screen.getByRole('tabpanel', { name: 'Informations' })
  expect(await within(panneau).findByText('zoom.us · visio')).toBeInTheDocument()
  expect(within(panneau).getByText('Capture')).toBeInTheDocument()
})

it('sans capture, l’onglet Information est inchangé', async () => {
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'visio.md', meta: { sensitivity: 'standard' }, body: 'T', content: 'T', has_audio: true,
  })
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="info" />)

  await waitFor(() => expect(getTranscript).toHaveBeenCalled())
  const panneau = screen.getByRole('tabpanel', { name: 'Informations' })
  expect(within(panneau).queryByText('Capture')).not.toBeInTheDocument()
})
