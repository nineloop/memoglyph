import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import type { AppConfig, LibraryItem } from '../api'
import { getTranscript, makeReport, streamJob, submitTranscribe } from '../api'
import { MeetingView } from '../components/MeetingView'
import { TranscribePanel } from '../components/TranscribePanel'
import { TranscriptReader } from '../components/TranscriptReader'
import type { Reunion } from '../lib/meetings'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    submitTranscribe: vi.fn(), streamJob: vi.fn(), getTranscript: vi.fn(), makeReport: vi.fn(),
  }
})

const config: AppConfig = {
  engines: ['whisper'], default_engine: 'whisper', default_project: 'Non classé',
  default_project_id: '01KY243SVP5GA31F6KSBYXEAHR', whisper_model: 'whisper-test',
  llm_model: 'llm-test', max_upload_mb: 2048, supported_extensions: ['.mp3'],
  diarization_available: true, llm_available: true,
}

beforeEach(() => {
  vi.mocked(submitTranscribe).mockReset().mockResolvedValue('job-1')
  vi.mocked(streamJob).mockReset().mockImplementation(() => vi.fn())
  vi.mocked(getTranscript).mockReset()
  vi.mocked(makeReport).mockReset()
})

// ---------- A1 : l'interface ne propose pas un CR sans horodatage ----------

it('sans horodatages, le compte-rendu automatique n’est pas proposé ni demandé', async () => {
  const user = userEvent.setup()
  const { container } = render(<TranscribePanel config={config} onCompleted={vi.fn()} />)
  fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement,
                   { target: { files: [new File(['a'], 'a.mp3', { type: 'audio/mpeg' })] } })
  const cr = screen.getByRole('checkbox', { name: /Compte-rendu automatique/ })
  if (!(cr as HTMLInputElement).checked) await user.click(cr)
  expect(cr).toBeChecked()

  await user.click(screen.getByRole('checkbox', { name: /Horodatages dans le transcript/ }))

  expect(cr).not.toBeChecked()
  expect(cr).toBeDisabled()
  expect(screen.getByText('Exige les horodatages (options avancées)')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
  await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
  expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({ report_on: false, timestamps: false })
})

it('un transcript sans horodatage ne propose pas de générer un compte-rendu', async () => {
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'sans-heure.md', body: 'Texte sans horodatage', content: '',
    meta: { pipeline: { stt: { timestamps: false } } },
  })
  render(<TranscriptReader name="sans-heure.md" documentScope="p" onBack={vi.fn()} />)
  await screen.findByText('Texte sans horodatage')

  const bouton = screen.getByRole('button', { name: /Compte-rendu/ })
  expect(bouton).toBeDisabled()
  expect(bouton).toHaveAttribute('title', expect.stringMatching(/sans horodatage/))
})

it('un transcript horodaté garde le bouton de compte-rendu', async () => {
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'horodate.md', body: '[00:00:01] Bonjour', content: '',
    meta: { pipeline: { stt: { timestamps: true } } },
  })
  render(<TranscriptReader name="horodate.md" documentScope="p" onBack={vi.fn()} />)
  await screen.findByText(/Bonjour/)

  expect(screen.getByRole('button', { name: /Compte-rendu/ })).toBeEnabled()
})

// ---------- A2 : la réserve d'un CR échoué reste visible ----------

function item(name: string): LibraryItem {
  return {
    name, id: `${name}-id`, meeting_id: 'meeting-id', project_id: 'project-id', title: 'r',
    date: '2026-10-21', duration: '00:13:00', language: 'en', engine: 'whisper', source: 'r.wav',
    project: 'Non classé', diarization: false, profiled: false, n_speakers: null, type: null,
    source_transcript: null, source_document_id: null, warnings: 0, size: 1, modified: 1,
  }
}

const reunion: Reunion = {
  cle: 'meeting-id', source: 'r.wav', titre: 'r', date: '2026-10-21', duree: '00:13:00',
  langue: 'en', projet: 'Non classé', nSpeakers: null, avertissements: 0,
  transcripts: [item('r.md')], comptesRendus: [], retenu: item('r.md'),
}

it('l’onglet Information affiche la cause d’un compte-rendu en échec', async () => {
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'r.md', body: 'T', content: 'T', has_audio: true,
    meta: { report_error: "Le service LLM (mlx) n'est pas disponible." },
  })
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="info" />)

  const panneau = screen.getByRole('tabpanel', { name: 'Informations' })
  expect(await within(panneau).findByText('Compte-rendu en échec')).toBeInTheDocument()
  expect(within(panneau).getByRole('note')).toHaveTextContent("Le service LLM (mlx) n'est pas disponible.")
})

it('sans échec de compte-rendu, aucune réserve n’est affichée', async () => {
  vi.mocked(getTranscript).mockResolvedValue({ name: 'r.md', body: 'T', content: 'T', meta: {} })
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="info" />)

  await waitFor(() => expect(getTranscript).toHaveBeenCalled())
  expect(screen.queryByText('Compte-rendu en échec')).not.toBeInTheDocument()
})
