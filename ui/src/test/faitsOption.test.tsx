import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import type { LibraryItem } from '../api'
import type { Reunion } from '../lib/meetings'
import { getConfig, getTranscript } from '../api'
import { MeetingView } from '../components/MeetingView'

// Les faits de la réunion sont une option (`MEMOGLYPH_FAITS=1`, exposée par `/api/config`) :
// coupée, l'onglet « Faits » n'existe pas ; activée, il est là comme avant.
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, getConfig: vi.fn(), getTranscript: vi.fn() }
})

const transcript: LibraryItem = {
  name: 'transcript-a.md', id: 'transcript-a-id', meeting_id: 'meeting-id', project_id: 'project-id',
  title: 'weekly-team-sync', date: '2026-07-21', duration: '00:42:00', language: 'fr', engine: 'whisper',
  source: 'Weekly Team Sync.mp4', project: 'Non classé', diarization: true, profiled: true, n_speakers: 4,
  type: null, source_transcript: null, source_document_id: null, warnings: 0, size: 100, modified: 1,
}
const reunion: Reunion = {
  cle: 'meeting-id', source: 'Weekly Team Sync.mp4', titre: 'weekly-team-sync', date: '2026-07-21',
  duree: '00:42:00', langue: 'fr', projet: 'Non classé', nSpeakers: 4, avertissements: 0,
  transcripts: [transcript], comptesRendus: [], retenu: transcript,
}

beforeEach(() => {
  vi.mocked(getTranscript).mockResolvedValue({ name: 'transcript-a.md', meta: {}, body: 'Transcript',
    content: '[00:00] Speaker 1 : Bonjour', has_audio: true } as never)
})

it('option coupée : aucun onglet « Faits »', async () => {
  vi.mocked(getConfig).mockResolvedValue({ faits: false } as never)
  render(<MeetingView reunion={reunion} onBack={vi.fn()} />)
  await waitFor(() => expect(getConfig).toHaveBeenCalled())
  expect(await screen.findByRole('tab', { name: /Transcription|Transcript/ })).toBeTruthy()
  expect(screen.queryByRole('tab', { name: /^Faits$|^Facts$/ })).toBeNull()
  expect(screen.queryByRole('tabpanel', { name: /Faits de la réunion|Meeting facts/ })).toBeNull()
})

it('configuration illisible : l’onglet reste masqué', async () => {
  vi.mocked(getConfig).mockRejectedValue(new Error('hors ligne'))
  render(<MeetingView reunion={reunion} onBack={vi.fn()} />)
  await waitFor(() => expect(getConfig).toHaveBeenCalled())
  expect(screen.queryByRole('tab', { name: /^Faits$|^Facts$/ })).toBeNull()
})

it('option activée : l’onglet « Faits » est là', async () => {
  vi.mocked(getConfig).mockResolvedValue({ faits: true } as never)
  render(<MeetingView reunion={reunion} onBack={vi.fn()} />)
  expect(await screen.findByRole('tab', { name: /^Faits$|^Facts$/ })).toBeTruthy()
  expect(getConfig).toHaveBeenCalled()
})
