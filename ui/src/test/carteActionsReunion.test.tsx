/** La carte « Actions de la réunion » n'existe que s'il y a une action à y mettre. */
import { render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import type { LibraryItem } from '../api'
import type { Reunion } from '../lib/meetings'
import { getTranscript } from '../api'
import { MeetingView } from '../components/MeetingView'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, getTranscript: vi.fn() }
})

function doc(name: string): LibraryItem {
  return {
    name, id: `${name}-id`, meeting_id: 'meeting-id', project_id: 'project-id', title: 'réunion', date: '2026-07-21',
    duration: '00:42:00', language: 'fr', engine: 'whisper', source: 'r.mp4', project: 'Non classé', diarization: true,
    profiled: true, n_speakers: 2, type: null, source_transcript: null, source_document_id: null, warnings: 0, size: 100,
    modified: 1,
  }
}

const base = {
  cle: 'meeting-id', source: 'r.mp4', titre: 'réunion', date: '2026-07-21', duree: '00:42:00', langue: 'fr',
  projet: 'Non classé', nSpeakers: 2, avertissements: 0, comptesRendus: [] as LibraryItem[],
}

beforeEach(() => {
  vi.mocked(getTranscript).mockResolvedValue({ name: 'a.md', content: '', meta: {}, voices: {} } as never)
})

it('un seul transcript : pas de carte vide', () => {
  const reunion: Reunion = { ...base, transcripts: [doc('a.md')], retenu: doc('a.md') }
  render(<MeetingView reunion={reunion} onBack={vi.fn()} onVoirDoublons={vi.fn()} initialTab="info" />)
  expect(screen.queryByText('Actions de la réunion')).toBeNull()
})

it('deux transcripts : la carte porte son bouton', () => {
  const reunion: Reunion = { ...base, transcripts: [doc('a.md'), doc('b.md')], retenu: doc('a.md') }
  render(<MeetingView reunion={reunion} onBack={vi.fn()} onVoirDoublons={vi.fn()} initialTab="info" />)
  expect(screen.getByText('Actions de la réunion')).toBeTruthy()
})
