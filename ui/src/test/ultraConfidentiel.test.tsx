import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AppConfig, LibraryItem } from '../api'
import { ask, getConfig, getFaitsReunion, getReglagesTraitement, getTranscript, lancerCompteRendu, makeReport } from '../api'
import { AskPanel } from '../components/AskPanel'
import { EclFaits } from '../components/EclFaits'
import { LangProvider } from '../components/LangProvider'
import { TranscribePanel } from '../components/TranscribePanel'
import { MeetingView } from '../components/MeetingView'
import type { Reunion } from '../lib/meetings'

// Un document ultra-confidentiel ne part vers aucun fournisseur cloud : l'écran ne le
// propose pas, et l'API le refuse (`tests/integration/test_ultra_confidentiel.py`).

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual, ask: vi.fn(), getConfig: vi.fn(), getFaitsReunion: vi.fn(),
    getTranscript: vi.fn(), makeReport: vi.fn(), getReglagesTraitement: vi.fn(), lancerCompteRendu: vi.fn(),
  }
})

const config: AppConfig = {
  engines: ['whisper', 'mistral'], default_engine: 'whisper', default_project: 'Non classé',
  default_project_id: 'P1', whisper_model: 'w', llm_model: 'l', max_upload_mb: 2048,
  supported_extensions: ['.wav'], diarization_available: true, llm_available: true,
  llm_providers: ['mlx', 'mistral', 'openai', 'anthropic'], local_providers: ['mlx'], default_llm_provider: 'mlx',
  llm_ready: { mlx: true, mistral: true, openai: true, anthropic: true },
  sensitivity_levels: ['ultra_confidential', 'confidential', 'standard'], default_sensitivity: 'standard',
} as AppConfig

beforeEach(() => {
  vi.mocked(getConfig).mockResolvedValue(config)
  vi.mocked(getReglagesTraitement).mockResolvedValue({ reglages: null })
  vi.mocked(lancerCompteRendu).mockReset().mockResolvedValue({ job_id: 'j1' })
  localStorage.clear()
})

// Une réunion dont le transcript est ultra-confidentiel (la sensibilité vient de la réunion, pas du compte rendu).
const transcriptUltra = {
  name: 'rachat.md', id: 'rachat-id', meeting_id: 'M1', project_id: 'P1', title: 'rachat', date: '2026-11-05', duration: '00:10:00',
  language: 'fr', engine: 'whisper', source: 'rachat.wav', project: 'Équipe', diarization: false, profiled: false, n_speakers: null,
  type: null, source_transcript: null, source_document_id: null, warnings: 0, size: 1, modified: 1,
} as LibraryItem
const reunionUltra: Reunion = {
  cle: 'M1', source: 'rachat.wav', titre: 'rachat', date: '2026-11-05', duree: '00:10:00', langue: 'fr', projet: 'Équipe', nSpeakers: null,
  avertissements: 0, transcripts: [transcriptUltra], comptesRendus: [], retenu: transcriptUltra,
}

describe.each([
  ['fr', 'Sensibilité du document', 'En ligne', 'Où traiter', 'Local uniquement', 'Non autorisé pour la sensibilité de ce document'],
  ['en', 'Document sensitivity', 'Online', 'Where to process', 'Local only', 'Not allowed for this document’s sensitivity'],
] as const)('Transcrire (%s)', (lang, champ, enLigne, legende, localSeul, raison) => {
  it('un document ultra-confidentiel ne propose pas le cloud', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <LangProvider initiale={lang}><TranscribePanel config={config} onCompleted={vi.fn()} /></LangProvider>)
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement,
                     { target: { files: [new File(['a'], 'rachat.wav', { type: 'audio/wav' })] } })
    const groupe = screen.getByRole('group', { name: legende })
    await user.click(within(groupe).getByRole('button', { name: new RegExp(`^${enLigne}`) }))
    const enLigneRadios = () => within(groupe).getAllByRole('radio').filter((r) => !/MLX/.test(r.closest('label')?.textContent ?? ''))
    expect(enLigneRadios().length).toBeGreaterThan(0)
    for (const radio of enLigneRadios()) expect(radio).not.toBeDisabled()
    await user.selectOptions(screen.getByRole('combobox', { name: champ }), 'ultra_confidential')
    for (const radio of enLigneRadios()) expect(radio).toBeDisabled()                  // aucun fournisseur en ligne ne se choisit
    expect(within(groupe).getByText(new RegExp(localSeul))).toBeInTheDocument()       // et la section le dit
    expect(within(groupe).getAllByText(new RegExp(raison)).length).toBeGreaterThan(0)  // chaque ligne dit pourquoi
  })
})

it('un document ultra-confidentiel génère son compte-rendu en local, même si le cloud est choisi', async () => {
  // Le chemin de la personne : la réunion, l'onglet Transcript, « Rédiger le compte rendu », le panneau, le lancement.
  const user = userEvent.setup()
  vi.mocked(getReglagesTraitement).mockResolvedValue({
    reglages: { fournisseur: 'openai', langue: '', resultats: { compte_rendu: true, titre_resume: true, locuteurs: false } } })
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'rachat.md', meta: { sensitivity: 'ultra_confidential' }, sensitivity_effective: 'ultra_confidential',
    body: 'Texte', content: '# Rachat',
  } as never)
  render(<LangProvider initiale="fr"><MeetingView reunion={reunionUltra} onBack={vi.fn()} /></LangProvider>)
  await screen.findByText('Texte')
  await user.click(screen.getByRole('button', { name: 'Rédiger le compte rendu' }))

  // Le fournisseur en ligne retenu est refusé : le local est présélectionné, et seul lui part.
  const choix = await screen.findByRole('radio', { name: /MLX/ })
  expect(choix).toBeChecked()
  expect(screen.getByRole('radio', { name: /OpenAI/ })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: 'Rédiger ce compte rendu' }))
  await waitFor(() => expect(lancerCompteRendu).toHaveBeenCalledWith('rachat.md', 'P1', 'mlx'))
  expect(makeReport).not.toHaveBeenCalled()
})

describe.each([
  ['fr', 'Réunion ultra-confidentielle : seuls les fournisseurs locaux sont proposés.'],
  ['en', 'Ultra-confidential meeting: only local providers are offered.'],
] as const)('Faits (%s)', (lang, note) => {
  it('la relance d’une réunion ultra-confidentielle ne propose que le local', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [] })
    render(<LangProvider initiale={lang}>
      <EclFaits meetingId="M1" viewScope="P1" onSource={() => {}} ultra />
    </LangProvider>)
    expect(await screen.findByText(note)).toBeInTheDocument()
    const options = (await screen.findAllByRole('option')) as HTMLOptionElement[]
    expect(options.map((o) => o.value)).toEqual(['mlx'])
  })
})

describe.each([
  ['fr', 'Poser une question sur vos transcripts…', 'Demander',
    '2 passage(s) ultra-confidentiel(s) restés en local : le fournisseur en ligne ne les a pas reçus.'],
  ['en', 'Ask a question about your transcripts…', 'Ask',
    '2 ultra-confidential passage(s) stayed local: the online provider did not receive them.'],
] as const)('Assistant (%s)', (lang, invite, bouton, note) => {
  it('dit combien de passages ultra-confidentiels sont restés locaux', async () => {
    const user = userEvent.setup()
    vi.mocked(ask).mockResolvedValue({
      answer: 'Le budget est validé [1].', passages_ecartes: 2,
      sources: [{ n: 1, name: 'point.md', title: 'Point', ts: null, rang: 1, excerpt: 'Budget validé',
                  document_id: 'D2', meeting_id: 'M2', project_id: 'P1', project: 'Client' }],
    })
    render(<LangProvider initiale={lang}><AskPanel viewScope="P1" scopeLabel="Client" /></LangProvider>)
    await user.type(screen.getByPlaceholderText(invite), 'Le budget ?')
    await user.click(screen.getByRole('button', { name: bouton }))
    expect(await screen.findByText(note)).toBeInTheDocument()
  })
})
