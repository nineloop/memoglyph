import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AppConfig } from '../api'
import { ask, getConfig, getFaitsReunion, getTranscript, makeReport } from '../api'
import { AskPanel } from '../components/AskPanel'
import { EclFaits } from '../components/EclFaits'
import { LangProvider } from '../components/LangProvider'
import { TranscribePanel } from '../components/TranscribePanel'
import { TranscriptReader } from '../components/TranscriptReader'
import { AudioProvider } from '../audio/AudioProvider'

// Un document ultra-confidentiel ne part vers aucun fournisseur cloud : l'écran ne le
// propose pas, et l'API le refuse (`tests/integration/test_ultra_confidentiel.py`).

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual, ask: vi.fn(), getConfig: vi.fn(), getFaitsReunion: vi.fn(),
    getTranscript: vi.fn(), makeReport: vi.fn(),
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
  localStorage.clear()
})

describe.each([
  ['fr', 'Sensibilité du document', /Traitement cloud/, 'Indisponible pour un document ultra-confidentiel.'],
  ['en', 'Document sensitivity', /Cloud processing/, 'Unavailable for an ultra-confidential document.'],
] as const)('Transcrire (%s)', (lang, champ, cloud, indisponible) => {
  it('un document ultra-confidentiel ne propose pas le cloud', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <LangProvider initiale={lang}><TranscribePanel config={config} onCompleted={vi.fn()} /></LangProvider>)
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement,
                     { target: { files: [new File(['a'], 'rachat.wav', { type: 'audio/wav' })] } })
    const boutonCloud = screen.getAllByRole('button').find((b) => cloud.test(b.textContent || ''))!
    expect(boutonCloud).not.toBeDisabled()
    await user.selectOptions(screen.getByRole('combobox', { name: champ }), 'ultra_confidential')
    expect(boutonCloud).toBeDisabled()
    expect(boutonCloud).toHaveTextContent(indisponible)
  })
})

it('un document ultra-confidentiel génère son compte-rendu en local, même si le cloud est choisi', async () => {
  const user = userEvent.setup()
  localStorage.setItem('memoglyph.llmProvider', 'openai')
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'rachat.md', meta: { sensitivity: 'ultra_confidential' }, body: 'Texte', content: '# Rachat',
  })
  vi.mocked(makeReport).mockResolvedValue({ name: 'rachat-cr.md', content: '# CR', genre: 'projet' })
  render(<LangProvider initiale="fr"><AudioProvider>
    <TranscriptReader name="rachat.md" documentScope="P1" onBack={vi.fn()} />
  </AudioProvider></LangProvider>)
  await screen.findByText('Texte')
  await user.click(screen.getByRole('button', { name: /Compte-rendu/ }))
  await waitFor(() => expect(makeReport).toHaveBeenCalledWith('rachat.md', 'P1', 'mlx'))
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
  ['fr', 'Pose une question sur tes transcripts…', 'Demander',
    '2 passage(s) ultra-confidentiel(s) restés sur ce Mac : le fournisseur cloud ne les a pas reçus.'],
  ['en', 'Ask a question about your transcripts…', 'Ask',
    '2 ultra-confidential passage(s) stayed on this Mac: the cloud provider did not receive them.'],
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
