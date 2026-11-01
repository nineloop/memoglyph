import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import type { AppConfig, LibraryItem } from '../api'
import { getConfig, getTranscript, updateSensitivity } from '../api'
import { LangProvider } from '../components/LangProvider'
import { MeetingView } from '../components/MeetingView'
import type { Reunion } from '../lib/meetings'

// La sensibilité est celle de la RÉUNION : l'espace Réunion affiche le niveau effectif
// que le serveur applique (`sensitivity_effective`), pas le niveau stocké sur le seul
// transcript retenu, et un changement affiche le niveau que le serveur a appliqué.

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, getConfig: vi.fn(), getTranscript: vi.fn(), updateSensitivity: vi.fn() }
})

function item(name: string, modified: number, type: string | null = null): LibraryItem {
  return {
    name, id: `${name}-id`, meeting_id: 'M1', project_id: 'P1', title: 'Rachat', date: '2026-07-21',
    duration: '00:42:00', language: 'fr', engine: 'whisper', source: 'rachat.mp4', project: 'Non classé',
    diarization: type !== 'report', profiled: false, n_speakers: 2, type,
    source_transcript: type === 'report' ? 'rachat-v2.md' : null,
    source_document_id: type === 'report' ? 'rachat-v2.md-id' : null, warnings: 0, size: 1, modified,
  }
}

// L'ancienne transcription est ultra-confidentielle, la plus récente (retenue) standard.
const retenu = item('rachat-v2.md', 200)
const reunion: Reunion = {
  cle: 'M1', source: 'rachat.mp4', titre: 'Rachat', date: '2026-07-21', duree: '00:42:00', langue: 'fr',
  projet: 'Non classé', nSpeakers: 2, avertissements: 0,
  transcripts: [retenu, item('rachat-v1.md', 100)], comptesRendus: [item('rachat-cr.md', 300, 'report')], retenu,
}

beforeEach(() => {
  vi.mocked(getConfig).mockResolvedValue({ faits: false } as AppConfig)
  vi.mocked(getTranscript).mockImplementation(async (name) => ({
    name, body: 'Texte', content: '[00:00] Speaker 1 : Bonjour',
    meta: { sensitivity: name === 'rachat-v1.md' ? 'ultra_confidential' : 'standard' },
    sensitivity_effective: 'ultra_confidential',
  }))
})

async function ouvrirInformations() {
  const user = userEvent.setup()
  render(<LangProvider><MeetingView reunion={reunion} onBack={vi.fn()} /></LangProvider>)
  await user.click(await screen.findByRole('tab', { name: /Informations/ }))
  return { user, choix: screen.getByRole('combobox', { name: 'Sensibilité' }) as HTMLSelectElement }
}

it('affiche la protection effective de la réunion, pas le niveau stocké du transcript retenu', async () => {
  const { choix } = await ouvrirInformations()
  await waitFor(() => expect(choix.value).toBe('ultra_confidential'))
  expect(screen.getByText(/toute la réunion/)).toBeInTheDocument()
})

it('après un changement, affiche le niveau que le serveur applique à la réunion', async () => {
  vi.mocked(updateSensitivity).mockResolvedValue({ name: 'rachat-v2.md', sensitivity: 'confidential' })
  const { user, choix } = await ouvrirInformations()
  await waitFor(() => expect(choix.value).toBe('ultra_confidential'))

  // Demande « standard », le serveur répond le niveau effectif : c'est lui qui s'affiche.
  await user.selectOptions(choix, 'standard')

  expect(updateSensitivity).toHaveBeenCalledWith('rachat-v2.md', 'standard', 'P1')
  await waitFor(() => expect(choix.value).toBe('confidential'))
})

it('un échec rétablit le niveau effectif précédent et le dit', async () => {
  vi.mocked(updateSensitivity).mockRejectedValue(new Error('refusé'))
  const { user, choix } = await ouvrirInformations()
  await waitFor(() => expect(choix.value).toBe('ultra_confidential'))
  await user.selectOptions(choix, 'standard')
  await waitFor(() => expect(choix.value).toBe('ultra_confidential'))
  expect(await screen.findByText(/Sensibilité non enregistrée/)).toBeInTheDocument()
})
