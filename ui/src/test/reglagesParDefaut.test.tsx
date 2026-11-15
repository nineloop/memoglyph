/**
 * L'écran Transcrire et les réglages par défaut du COMPTE : lus une fois à l'ouverture (jamais par le navigateur), appliqués par
 * le même chemin que le clic (le moteur de transcription suit le fournisseur), sans écraser un choix déjà fait, sans jamais
 * écrire à l'ouverture ; « Retenir comme réglages par défaut » écrit le fournisseur, la langue valide, les résultats voulus et la
 * sensibilité seulement si elle est plus stricte que le défaut (voir `sensibiliteRetenue.test.tsx`), et rien d'autre.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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

const CONFIG: AppConfig = {
  engines: ['whisper', 'mistral'], default_engine: 'whisper', default_project: 'Non classé',
  default_project_id: '01KY243SVP5GA31F6KSBYXEAHR', whisper_model: 'w', llm_model: 'l', max_upload_mb: 2048,
  supported_extensions: ['.mp3'], diarization_available: true, llm_available: true,
  llm_providers: ['mistral', 'mlx', 'openai'], local_providers: ['mlx'], default_llm_provider: 'mlx',
  llm_ready: { mlx: true, mistral: true, openai: true },
  llm_models: { mlx: 'qwen3-4b-mlx', mistral: 'mistral-chat-test', openai: 'gpt-test' },
  sensitivity_levels: ['ultra_confidential', 'confidential', 'standard'], default_sensitivity: 'standard',
} as AppConfig

const retenus = (sur: Partial<ReglagesTraitement> = {}) => ({
  reglages: { fournisseur: 'openai', langue: '', resultats: { compte_rendu: true, titre_resume: true, locuteurs: false }, ...sur },
})

function monter(config: AppConfig = CONFIG) {
  const rendu = render(<LangProvider initiale="fr"><TranscribePanel config={config} onCompleted={vi.fn()} /></LangProvider>)
  const input = rendu.container.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [new File(['audio'], 'visio.mp3', { type: 'audio/mpeg' })] } })
  return rendu
}

const lancer = () => userEvent.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
const envoi = () => vi.mocked(submitTranscribe).mock.calls[0][1]

beforeEach(() => {
  localStorage.clear()
  vi.mocked(submitTranscribe).mockReset().mockResolvedValue('job-1')
  vi.mocked(streamJob).mockReset().mockImplementation(() => vi.fn())
  vi.mocked(getReglagesTraitement).mockReset().mockResolvedValue({ reglages: null })
  vi.mocked(setReglagesTraitement).mockReset().mockImplementation((async (r: ReglagesTraitement) => ({ reglages: r })) as never)
})

/** Le sélecteur : « En ligne » s'ouvre une fois, puis on coche un fournisseur par son nom. */
async function choisir(nom: RegExp) {
  const bascule = screen.getByRole('button', { name: /^En ligne/ })
  if (bascule.getAttribute('aria-expanded') !== 'true') await userEvent.click(bascule)
  await userEvent.click(screen.getByRole('radio', { name: nom }))
}

describe('à l\'ouverture', () => {
  it('applique le fournisseur retenu par le compte, par le même chemin que le choix : le fournisseur en ligne retenu est coché', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ fournisseur: 'openai' }))
    monter()

    await waitFor(() => expect(screen.getByRole('radio', { name: /OpenAI/ })).toBeChecked())
    await lancer()
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(envoi()).toMatchObject({ engine: 'whisper', llm_provider: 'openai' })       // OpenAI n'a pas de transcription : whisper reste
  })

  it('Mistral retenu : la transcription suit vers Voxtral sans clic ; sans moteur Voxtral déclaré, whisper', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ fournisseur: 'mistral' }))
    const { unmount } = monter()
    await waitFor(() => expect(screen.getByRole('radio', { name: /Mistral/ })).toBeChecked())
    await lancer()
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(envoi()).toMatchObject({ engine: 'mistral', llm_provider: 'mistral' })
    unmount()

    vi.mocked(submitTranscribe).mockClear()
    monter({ ...CONFIG, engines: ['whisper'] })
    await waitFor(() => expect(screen.getByRole('radio', { name: /Mistral/ })).toBeChecked())
    await lancer()
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(envoi()).toMatchObject({ engine: 'whisper', llm_provider: 'mistral' })
  })

  it('un fournisseur retenu devenu périmé ou non prêt retombe sur « Local », sans écrire au compte', async () => {
    for (const absent of ['disparu', 'anthropic']) {
      vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ fournisseur: absent }))
      vi.mocked(submitTranscribe).mockClear()
      const { unmount } = monter()
      await waitFor(() => expect(getReglagesTraitement).toHaveBeenCalled())
      await lancer()
      await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
      expect(envoi()).toMatchObject({ llm_provider: 'mlx', engine: 'whisper' })
      unmount()
    }
    expect(setReglagesTraitement).not.toHaveBeenCalled()
  })

  it('lire les réglages, choisir un fournisseur ou un réglage n\'écrit JAMAIS au compte : seul « Retenir » écrit', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ fournisseur: 'mistral' }))
    monter()
    await waitFor(() => expect(screen.getByRole('radio', { name: /Mistral/ })).toBeChecked())
    await choisir(/OpenAI/)
    await userEvent.click(screen.getByRole('checkbox', { name: /Compte rendu automatique/ }))
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Langue parlée' }), 'es')
    expect(setReglagesTraitement).not.toHaveBeenCalled()
  })

  it('des réglages illisibles (serveur muet) : les valeurs de départ, l\'écran reste utilisable', async () => {
    vi.mocked(getReglagesTraitement).mockRejectedValue(new Error('hors ligne'))
    monter()
    await lancer()
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(envoi()).toMatchObject({ llm_provider: 'mlx', engine: 'whisper' })
  })

  it('un choix fait AVANT l\'arrivée des réglages n\'est pas écrasé par eux', async () => {
    let arrivee: (valeur: { reglages: ReglagesTraitement | null }) => void = () => {}
    vi.mocked(getReglagesTraitement).mockReturnValue(new Promise((resolve) => { arrivee = resolve }))
    monter()
    await choisir(/OpenAI/)

    arrivee(retenus({ fournisseur: 'mistral' }))
    await waitFor(() => expect(getReglagesTraitement).toHaveBeenCalled())
    await new Promise((fin) => setTimeout(fin, 0))
    await lancer()
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(envoi()).toMatchObject({ llm_provider: 'openai' })              // le choix de la personne, pas celui du compte
  })

  it('un document ultra-confidentiel choisi avant l\'arrivée des réglages reste local : le fournisseur en ligne retenu ne s\'applique pas', async () => {
    let arrivee: (valeur: { reglages: ReglagesTraitement | null }) => void = () => {}
    vi.mocked(getReglagesTraitement).mockReturnValue(new Promise((resolve) => { arrivee = resolve }))
    monter()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Sensibilité du document' }), 'ultra_confidential')

    arrivee(retenus({ fournisseur: 'openai' }))
    await new Promise((fin) => setTimeout(fin, 0))
    await lancer()
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(envoi()).toMatchObject({ llm_provider: 'mlx', engine: 'whisper', sensitivity: 'ultra_confidential' })
  })

  it('applique aussi la langue et les résultats voulus', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({
      fournisseur: 'mlx', langue: 'es', resultats: { compte_rendu: false, titre_resume: false, locuteurs: false } }))
    monter()

    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Langue parlée' })).toHaveValue('es'))
    expect(screen.getByRole('checkbox', { name: /Compte rendu automatique/ })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Résumé et thèmes/ })).not.toBeChecked()
    await lancer()
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(envoi()).toMatchObject({ language: 'es', report_on: false, profile: false })
  })

  it('une langue retenue hors de la liste rouvre « Autre » avec ce code', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ fournisseur: 'mlx', langue: 'pt' }))
    monter()
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Langue parlée' })).toHaveValue('__other__'))
    expect(screen.getByRole('textbox', { name: 'Code langue Whisper' })).toHaveValue('pt')
  })
})

describe('un fournisseur retenu mais pas configuré se dit, avec son nom, en une ligne', () => {
  const sansOpenai = { ...CONFIG, llm_ready: { mlx: true, mistral: true, openai: false } } as AppConfig
  const LIGNE = 'OpenAI, retenu pour votre compte, n’est pas configuré : le traitement se fera en Local.'

  it('la ligne nomme le fournisseur, et l\'envoi part en Local (jamais un autre fournisseur en ligne)', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ fournisseur: 'openai' }))
    monter(sansOpenai)

    expect(await screen.findByText(LIGNE)).toBeInTheDocument()
    expect(screen.queryByText(/\.env/)).not.toBeInTheDocument()                    // aucun jargon : pas de nom de fichier de configuration
    await lancer()
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(envoi()).toMatchObject({ llm_provider: 'mlx', engine: 'whisper' })
  })

  it('pas de ligne quand le fournisseur retenu est prêt, disparu, ou quand rien n\'est retenu', async () => {
    for (const reglages of [retenus({ fournisseur: 'mistral' }), retenus({ fournisseur: 'disparu' }), { reglages: null }]) {
      vi.mocked(getReglagesTraitement).mockResolvedValue(reglages)
      const { unmount } = monter(sansOpenai)
      await waitFor(() => expect(getReglagesTraitement).toHaveBeenCalled())
      await new Promise((fin) => setTimeout(fin, 0))
      expect(screen.queryByText(/retenu pour votre compte/)).not.toBeInTheDocument()
      unmount()
    }
  })

  it('la ligne disparaît dès que la personne choisit elle-même un fournisseur (elle ne dirait plus vrai)', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenus({ fournisseur: 'openai' }))
    monter(sansOpenai)
    await screen.findByText(LIGNE)

    await choisir(/Mistral/)

    expect(screen.queryByText(LIGNE)).not.toBeInTheDocument()
  })
})

describe('« Retenir comme réglages par défaut »', () => {
  const retenir = () => userEvent.click(screen.getByRole('button', { name: 'Retenir comme réglages par défaut' }))

  it('écrit le fournisseur, la langue, les résultats voulus et la sensibilité (plus stricte que le défaut), et RIEN d\'autre', async () => {
    monter()
    await choisir(/OpenAI/)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Langue parlée' }), 'fr')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Sensibilité du document' }), 'confidential')

    await retenir()

    await waitFor(() => expect(setReglagesTraitement).toHaveBeenCalledTimes(1))
    const ecrit = vi.mocked(setReglagesTraitement).mock.calls[0][0]
    expect(ecrit).toEqual({ fournisseur: 'openai', langue: 'fr', resultats: { compte_rendu: true, titre_resume: true, locuteurs: expect.any(Boolean) },
      sensibilite: 'confidential' })
    expect(Object.keys(ecrit).sort()).toEqual(['fournisseur', 'langue', 'resultats', 'sensibilite'])
    expect(await screen.findByRole('status')).toHaveTextContent('Réglages retenus')
  })

  it('un code de langue libre qui n\'est pas un code valide n\'est pas envoyé : la langue retenue reste vide', async () => {
    monter()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Langue parlée' }), '__other__')
    await userEvent.type(screen.getByRole('textbox', { name: 'Code langue Whisper' }), 'français!')

    await retenir()

    await waitFor(() => expect(setReglagesTraitement).toHaveBeenCalledTimes(1))
    expect(vi.mocked(setReglagesTraitement).mock.calls[0][0].langue).toBe('')
  })

  it('un code libre valide est retenu tel quel', async () => {
    monter()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Langue parlée' }), '__other__')
    await userEvent.type(screen.getByRole('textbox', { name: 'Code langue Whisper' }), 'PT')

    await retenir()

    await waitFor(() => expect(setReglagesTraitement).toHaveBeenCalledTimes(1))
    expect(vi.mocked(setReglagesTraitement).mock.calls[0][0].langue).toBe('pt')
  })

  it('un refus du serveur se dit, sans perdre le formulaire', async () => {
    vi.mocked(setReglagesTraitement).mockRejectedValue(new Error('Connexion requise.'))
    monter()

    await retenir()

    expect(await screen.findByRole('alert')).toHaveTextContent('Les réglages n’ont pas pu être retenus')
    expect(screen.getAllByText('visio.mp3')[0]).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Lancer la transcription' })).toBeEnabled()
  })
})
