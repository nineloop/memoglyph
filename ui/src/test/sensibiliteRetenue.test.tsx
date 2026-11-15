/**
 * La sensibilité retenue par le compte : « Retenir » la garde SEULEMENT quand elle est plus stricte que le défaut de l'instance
 * (elle ne l'abaisse jamais) ; à l'ouverture, la plus stricte des deux s'applique, une ligne le dit, et le compte n'est jamais
 * réécrit à la lecture. Un ultra-confidentiel retenu grise « En ligne » comme n'importe quel ultra-confidentiel choisi.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AppConfig, ReglagesTraitement } from '../api'
import { getReglagesTraitement, setReglagesTraitement, streamJob, submitTranscribe } from '../api'
import { LangProvider } from '../components/LangProvider'
import { TranscribePanel } from '../components/TranscribePanel'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual, submitTranscribe: vi.fn(), streamJob: vi.fn(), getReglagesTraitement: vi.fn(), setReglagesTraitement: vi.fn(),
  }
})

const config = (defaut = 'standard'): AppConfig => ({
  engines: ['whisper'], default_engine: 'whisper', default_project: 'Non classé',
  default_project_id: '01KY243SVP5GA31F6KSBYXEAHR', whisper_model: 'w', llm_model: 'l', max_upload_mb: 2048,
  supported_extensions: ['.mp3'], diarization_available: true, llm_available: true,
  llm_providers: ['mistral', 'mlx', 'openai'], local_providers: ['mlx'], default_llm_provider: 'mlx',
  llm_ready: { mlx: true, mistral: true, openai: true },
  llm_models: { mlx: 'qwen3-4b-mlx', mistral: 'mistral-chat-test', openai: 'gpt-test' },
  sensitivity_levels: ['ultra_confidential', 'confidential', 'standard'], default_sensitivity: defaut,
} as AppConfig)

const retenus = (sur: Partial<ReglagesTraitement> = {}) => ({
  reglages: { fournisseur: 'mlx', langue: '', resultats: { compte_rendu: true, titre_resume: true, locuteurs: false }, ...sur },
})

function monter(defaut = 'standard', langue: 'fr' | 'en' = 'fr') {
  const rendu = render(<LangProvider initiale={langue}><TranscribePanel config={config(defaut)} onCompleted={vi.fn()} /></LangProvider>)
  fireEvent.change(rendu.container.querySelector('input[type="file"]') as HTMLInputElement,
                   { target: { files: [new File(['audio'], 'visio.mp3', { type: 'audio/mpeg' })] } })
  return rendu
}

const choixSensibilite = (nom = 'Sensibilité du document') => screen.getByRole('combobox', { name: nom }) as HTMLSelectElement
const retenir = () => userEvent.click(screen.getByRole('button', { name: 'Retenir comme réglages par défaut' }))

beforeEach(() => {
  localStorage.clear()
  vi.mocked(submitTranscribe).mockReset().mockResolvedValue('job-1')
  vi.mocked(streamJob).mockReset().mockImplementation(() => vi.fn())
  vi.mocked(getReglagesTraitement).mockReset().mockResolvedValue({ reglages: null })
  vi.mocked(setReglagesTraitement).mockReset().mockImplementation((async (r: ReglagesTraitement) => ({ reglages: r })) as never)
})

describe('« Retenir » et la sensibilité', () => {
  it('une sensibilité plus stricte que le défaut est écrite', async () => {
    monter()
    await userEvent.selectOptions(choixSensibilite(), 'confidential')
    await retenir()
    await waitFor(() => expect(setReglagesTraitement).toHaveBeenCalledTimes(1))
    expect(vi.mocked(setReglagesTraitement).mock.calls[0][0]).toMatchObject({ sensibilite: 'confidential' })
  })

  it('l\'ultra-confidentiel est écrit aussi', async () => {
    monter()
    await userEvent.selectOptions(choixSensibilite(), 'ultra_confidential')
    await retenir()
    await waitFor(() => expect(setReglagesTraitement).toHaveBeenCalledTimes(1))
    expect(vi.mocked(setReglagesTraitement).mock.calls[0][0]).toMatchObject({ sensibilite: 'ultra_confidential' })
  })

  it('une sensibilité égale au défaut n\'est pas écrite : la clé est absente, pas vide', async () => {
    monter()
    await retenir()
    await waitFor(() => expect(setReglagesTraitement).toHaveBeenCalledTimes(1))
    expect('sensibilite' in vi.mocked(setReglagesTraitement).mock.calls[0][0]).toBe(false)
  })

  it('un défaut « confidentiel » : retenir « confidentiel » (égal) ou « public » (en dessous) n\'écrit rien pour ce champ', async () => {
    monter('confidential')
    await retenir()
    await waitFor(() => expect(setReglagesTraitement).toHaveBeenCalledTimes(1))
    expect('sensibilite' in vi.mocked(setReglagesTraitement).mock.calls[0][0]).toBe(false)

    await userEvent.selectOptions(choixSensibilite(), 'standard')
    await retenir()
    await waitFor(() => expect(setReglagesTraitement).toHaveBeenCalledTimes(2))
    expect('sensibilite' in vi.mocked(setReglagesTraitement).mock.calls[1][0]).toBe(false)
  })

  it('un défaut « confidentiel » : retenir « ultra-confidentiel » (plus strict) est écrit', async () => {
    monter('confidential')
    await userEvent.selectOptions(choixSensibilite(), 'ultra_confidential')
    await retenir()
    await waitFor(() => expect(setReglagesTraitement).toHaveBeenCalledTimes(1))
    expect(vi.mocked(setReglagesTraitement).mock.calls[0][0]).toMatchObject({ sensibilite: 'ultra_confidential' })
  })
})

describe('à l\'ouverture', () => {
  it('une sensibilité retenue s\'applique, et une ligne le dit, en français et en anglais', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ sensibilite: 'confidential' }))
    const fr = monter()
    await waitFor(() => expect(choixSensibilite().value).toBe('confidential'))
    expect(screen.getByText('Sensibilité retenue pour votre compte : Confidentiel.')).toBeInTheDocument()
    fr.unmount()

    monter('standard', 'en')
    await waitFor(() => expect(choixSensibilite('Document sensitivity').value).toBe('confidential'))
    expect(screen.getByText('Sensitivity kept for your account: Confidential.')).toBeInTheDocument()
  })

  it('lire n\'écrit jamais au compte', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ sensibilite: 'confidential' }))
    monter()
    await waitFor(() => expect(choixSensibilite().value).toBe('confidential'))
    expect(setReglagesTraitement).not.toHaveBeenCalled()
  })

  it('le défaut relevé au-dessus du choix retenu gagne : la sensibilité est celle du défaut, sans ligne', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ sensibilite: 'confidential' }))
    monter('ultra_confidential')
    await waitFor(() => expect(getReglagesTraitement).toHaveBeenCalled())
    await new Promise((fin) => setTimeout(fin, 0))
    expect(choixSensibilite().value).toBe('ultra_confidential')
    expect(screen.queryByText(/Sensibilité retenue pour votre compte/)).not.toBeInTheDocument()
    expect(setReglagesTraitement).not.toHaveBeenCalled()                               // le compte n'est pas réécrit
  })

  it('une sensibilité retenue qui ne dépasse plus le défaut (égale ou en dessous) ne s\'applique pas, sans ligne', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ sensibilite: 'standard' }))
    monter('confidential')
    await new Promise((fin) => setTimeout(fin, 0))
    expect(choixSensibilite().value).toBe('confidential')
    expect(screen.queryByText(/Sensibilité retenue pour votre compte/)).not.toBeInTheDocument()
  })

  it('la ligne s\'efface dès que la personne choisit elle-même un autre niveau pour ce document', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ sensibilite: 'confidential' }))
    monter()
    await screen.findByText('Sensibilité retenue pour votre compte : Confidentiel.')
    await userEvent.selectOptions(choixSensibilite(), 'standard')
    expect(screen.queryByText(/Sensibilité retenue pour votre compte/)).not.toBeInTheDocument()
  })

  it('un choix fait AVANT l\'arrivée des réglages n\'est pas écrasé par la sensibilité retenue', async () => {
    let arrivee: (valeur: { reglages: ReglagesTraitement | null }) => void = () => {}
    vi.mocked(getReglagesTraitement).mockReturnValue(new Promise((resolve) => { arrivee = resolve }))
    monter()
    await userEvent.selectOptions(choixSensibilite(), 'ultra_confidential')
    arrivee(retenus({ sensibilite: 'confidential' }))
    await new Promise((fin) => setTimeout(fin, 0))
    expect(choixSensibilite().value).toBe('ultra_confidential')
    expect(screen.queryByText(/Sensibilité retenue pour votre compte/)).not.toBeInTheDocument()
  })

  it('l\'envoi part avec la sensibilité appliquée', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ sensibilite: 'confidential' }))
    monter()
    await screen.findByText('Sensibilité retenue pour votre compte : Confidentiel.')
    await userEvent.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({ sensitivity: 'confidential' })
  })
})

describe('un ultra-confidentiel retenu', () => {
  it('grise « En ligne » et laisse le local, comme un ultra-confidentiel choisi à la main', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ sensibilite: 'ultra_confidential' }))
    monter()
    await waitFor(() => expect(choixSensibilite().value).toBe('ultra_confidential'))
    const groupe = screen.getByRole('group', { name: 'Où traiter' })
    expect(within(groupe).getByText(/Local uniquement/)).toBeInTheDocument()
    await userEvent.click(within(groupe).getByRole('button', { name: /^En ligne/ }))
    expect(within(groupe).getByRole('radio', { name: /OpenAI/ })).toBeDisabled()
    expect(within(groupe).getByRole('radio', { name: /MLX/ })).toBeChecked()
  })

  it('même avec un fournisseur en ligne retenu, l\'ultra-confidentiel retenu reste local (rien de réécrit)', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ fournisseur: 'openai', sensibilite: 'ultra_confidential' }))
    monter()
    await waitFor(() => expect(choixSensibilite().value).toBe('ultra_confidential'))
    await new Promise((fin) => setTimeout(fin, 0))
    const groupe = screen.getByRole('group', { name: 'Où traiter' })
    expect(within(groupe).getByRole('radio', { name: /MLX/ })).toBeChecked()
    // Le fournisseur en ligne retenu n'a JAMAIS été appliqué, même un instant : « En ligne » n'est pas ouvert de lui-même.
    expect(within(groupe).getByRole('button', { name: /^En ligne/ })).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({ llm_provider: 'mlx', sensitivity: 'ultra_confidential' })
    expect(setReglagesTraitement).not.toHaveBeenCalled()
  })
})
