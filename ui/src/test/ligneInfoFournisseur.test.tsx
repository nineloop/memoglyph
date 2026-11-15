/**
 * Un fournisseur en ligne qui n'est pas celui qui transcrit en ligne : la transcription reste locale, seul le texte part chez lui.
 * L'écran le dit en une ligne. Mistral, lui, transcrit aussi en ligne : pas de ligne ; en local : pas de ligne.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AppConfig } from '../api'
import { getReglagesTraitement, streamJob, submitTranscribe } from '../api'
import { LangProvider } from '../components/LangProvider'
import { TranscribePanel } from '../components/TranscribePanel'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, submitTranscribe: vi.fn(), streamJob: vi.fn(), getReglagesTraitement: vi.fn() }
})

const CONFIG: AppConfig = {
  engines: ['whisper', 'mistral'], default_engine: 'whisper', default_project: 'Non classé',
  default_project_id: '01KY243SVP5GA31F6KSBYXEAHR', whisper_model: 'w', llm_model: 'l', max_upload_mb: 2048,
  supported_extensions: ['.mp3'], diarization_available: true, llm_available: true,
  llm_providers: ['mistral', 'mlx', 'openai', 'groq'], local_providers: ['mlx'], default_llm_provider: 'mlx',
  llm_ready: { mlx: true, mistral: true, openai: true, groq: true },
  llm_models: { mlx: 'qwen', mistral: 'mistral-chat', openai: 'gpt-test', groq: 'groq-test' },
  sensitivity_levels: ['ultra_confidential', 'confidential', 'standard'], default_sensitivity: 'standard',
} as AppConfig

const FR = 'La transcription reste locale ; le texte part chez le fournisseur.'
const EN = 'Transcription stays local; the text goes to the provider.'

function monter(langue: 'fr' | 'en' = 'fr') {
  const rendu = render(<LangProvider initiale={langue}><TranscribePanel config={CONFIG} onCompleted={vi.fn()} /></LangProvider>)
  fireEvent.change(rendu.container.querySelector('input[type="file"]') as HTMLInputElement,
                   { target: { files: [new File(['audio'], 'visio.mp3', { type: 'audio/mpeg' })] } })
  return rendu
}

beforeEach(() => {
  vi.mocked(submitTranscribe).mockReset().mockResolvedValue('job-1')
  vi.mocked(streamJob).mockReset().mockImplementation(() => vi.fn())
  vi.mocked(getReglagesTraitement).mockReset().mockResolvedValue({ reglages: null })
})

/** Le sélecteur : « En ligne » s'ouvre une fois, puis on coche un fournisseur par son nom. */
async function choisir(nom: RegExp) {
  const bascule = screen.getByRole('button', { name: /^(En ligne|Online)/ })
  if (bascule.getAttribute('aria-expanded') !== 'true') await userEvent.click(bascule)
  await userEvent.click(screen.getByRole('radio', { name: nom }))
}

describe('la ligne « la transcription reste locale »', () => {
  it('apparaît pour un fournisseur en ligne qui ne transcrit pas (OpenAI, Groq), en français et en anglais', async () => {
    monter()
    await choisir(/Mistral/)
    expect(screen.queryByText(FR)).not.toBeInTheDocument()                             // Mistral transcrit aussi en ligne

    await choisir(/OpenAI/)
    expect(screen.getByText(FR)).toBeInTheDocument()
    await choisir(/Groq/)
    expect(screen.getByText(FR)).toBeInTheDocument()
  })

  it('est aussi dite en anglais', async () => {
    monter('en')
    await choisir(/OpenAI/)
    expect(screen.getByText(EN)).toBeInTheDocument()
  })

  it('n\'apparaît ni en local, ni pour Mistral (qui transcrit aussi en ligne)', async () => {
    monter()
    expect(screen.queryByText(FR)).not.toBeInTheDocument()                             // « Local »
    await choisir(/Mistral/)
    await waitFor(() => expect(screen.getByRole('radio', { name: /Mistral/ })).toBeChecked())
    expect(screen.queryByText(FR)).not.toBeInTheDocument()                             // Mistral
    await choisir(/MLX/)
    expect(screen.queryByText(FR)).not.toBeInTheDocument()
  })

  it('disparaît quand on repasse en local', async () => {
    monter()
    await choisir(/OpenAI/)
    expect(screen.getByText(FR)).toBeInTheDocument()

    await choisir(/MLX/)

    expect(screen.queryByText(FR)).not.toBeInTheDocument()
  })
})
