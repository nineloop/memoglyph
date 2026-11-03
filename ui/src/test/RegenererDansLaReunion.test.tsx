import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import type { LibraryItem, ListeTraitements } from '../api'
import type { Reunion } from '../lib/meetings'
import { getConfig, getTranscript, lancerCompteRendu } from '../api'
import { MeetingView } from '../components/MeetingView'
import { TraitementsContext } from '../lib/traitementsContext'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, getTranscript: vi.fn(), getConfig: vi.fn(), lancerCompteRendu: vi.fn() }
})

function item(name: string, modified: number, type: string | null = null): LibraryItem {
  return {
    name, id: `${name}-id`, meeting_id: 'meeting-id', project_id: 'project-id', title: 'point-prix',
    date: '2026-11-03', duration: '00:42:00', language: 'fr', engine: 'whisper', source: 'Point prix.mp4',
    project: 'Non classé', diarization: type !== 'report', profiled: false, n_speakers: 2,
    type, source_transcript: type === 'report' ? 'transcript-a.md' : null,
    source_document_id: type === 'report' ? 'transcript-a.md-id' : null, warnings: 0, size: 100, modified,
  }
}

const transcript = item('transcript-a.md', 1000)
const ancien = item('report-a.md', 2000, 'report')
const nouveau = item('report-b.md', 3000, 'report')
const reunion: Reunion = {
  cle: 'meeting-id', source: 'Point prix.mp4', titre: 'point-prix', date: '2026-11-03', duree: '00:42:00',
  langue: 'fr', projet: 'Non classé', nSpeakers: 2, avertissements: 0,
  transcripts: [transcript], comptesRendus: [ancien], retenu: transcript,
}

beforeEach(() => {
  vi.mocked(getConfig).mockReset().mockResolvedValue({
    faits: false, llm_providers: ['mlx'], local_providers: ['mlx'], default_llm_provider: 'mlx',
    llm_ready: { mlx: true }, llm_models: { mlx: 'Qwen3-8B' } } as never)
  vi.mocked(lancerCompteRendu).mockReset().mockResolvedValue({ job_id: 'j9' })
  vi.mocked(getTranscript).mockImplementation(async (name) => ({
    name,
    meta: name.startsWith('report') ? { type: 'report', model: name === 'report-b.md' ? 'Qwen3-8B' : 'ancien-modele' } : {},
    body: name === 'report-b.md' ? '## Version régénérée' : name === 'report-a.md' ? '## Ancienne version' : 'Transcript',
    content: '',
  }) as never)
})

function vue(r: Reunion, liste: ListeTraitements | null, onChanged = vi.fn()) {
  return (
    <TraitementsContext.Provider value={{ liste, rafraichir: vi.fn(), suivre: vi.fn() }}>
      <MeetingView reunion={r} onBack={vi.fn()} onChanged={onChanged} />
    </TraitementsContext.Provider>
  )
}

it('régénérer depuis la réunion : la nouvelle version s’ajoute et devient celle qu’on lit', async () => {
  const user = userEvent.setup()
  const onChanged = vi.fn()
  const { rerender } = render(vue(reunion, null, onChanged))
  expect(await screen.findByRole('heading', { name: 'Ancienne version', level: 2 })).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: 'Régénérer' }))
  await user.click(await screen.findByRole('button', { name: 'Régénérer ce compte rendu' }))
  expect(lancerCompteRendu).toHaveBeenCalledWith('transcript-a.md', expect.any(String), 'mlx')

  const fini: ListeTraitements = {
    actifs: 0, en_attente: 0,
    traitements: [{
      id: 'j9', nature: 'compte-rendu', statut: 'done', en_attente: false, progression: 1, titre: 'point-prix',
      projet_id: 'project-id', projet_nom: 'Non classé', etape: 3, etapes: 3, message_code: null, message_params: {},
      debut: 0, meeting: { name: 'report-b.md' }, erreur: null, erreur_code: null, erreur_params: {},
    }],
  }
  rerender(vue(reunion, fini, onChanged))
  await waitFor(() => expect(onChanged).toHaveBeenCalled())
  // La bibliothèque est rechargée : la réunion liste maintenant les deux versions.
  rerender(vue({ ...reunion, comptesRendus: [nouveau, ancien] }, fini, onChanged))

  expect(await screen.findByRole('heading', { name: 'Version régénérée', level: 2 })).toBeInTheDocument()
  expect(screen.getByText('Nouvelle version prête.')).toBeInTheDocument()
  const lu = document.querySelector('.report-view') as HTMLElement
  expect(within(lu).getByText('Qwen3-8B')).toBeInTheDocument()     // le modèle servi, écrit dans la version lue
  expect(screen.getAllByRole('button', { name: /Afficher le compte-rendu \d du/ })).toHaveLength(2)   // aucune version perdue
})

it('en lecture seule (réunion partagée), aucun bouton « Régénérer »', async () => {
  const { PartageContext } = await import('../lib/partage')
  render(
    <PartageContext.Provider value={{ lectureSeule: true, peutPartager: () => false }}>
      {vue(reunion, null)}
    </PartageContext.Provider>,
  )
  await screen.findByRole('heading', { name: 'Ancienne version', level: 2 })
  expect(screen.queryByRole('button', { name: 'Régénérer' })).toBeNull()
})
