import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AppConfig, LibraryItem } from '../api'
import { getCloudUltra, getConfig, getEtatAuth, getFaitsReunion, getReglagesTraitement, getTranscript, lancerCompteRendu, makeReport, setCloudUltra } from '../api'
import { AuthGate } from '../components/AuthGate'
import { BoutonConfidentialite, ConfidentialitePanel } from '../components/ConfidentialitePanel'
import { EclFaits } from '../components/EclFaits'
import { LangProvider } from '../components/LangProvider'
import { TranscribePanel } from '../components/TranscribePanel'
import { MeetingView } from '../components/MeetingView'
import type { Reunion } from '../lib/meetings'
import { aucuneFenetreDeConfirmation, avecConfirmation, confirmer as confirmerLaFenetre } from './confirmation'

// Le réglage « cloud pour mon ultra-confidentiel » du compte : inactif par défaut,
// confirmé pour être activé, pas pour être désactivé. Les écrans ne proposent le
// cloud pour un document ultra-confidentiel que s'il est actif ; le serveur reste
// juge (`tests/integration/test_cloud_par_compte.py`).

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual, getCloudUltra: vi.fn(), setCloudUltra: vi.fn(), getEtatAuth: vi.fn(), getConfig: vi.fn(),
    getFaitsReunion: vi.fn(), getTranscript: vi.fn(), makeReport: vi.fn(), getReglagesTraitement: vi.fn(), lancerCompteRendu: vi.fn(),
  }
})

const INACTIF = { autorise: false, modifie_le: null }
const ACTIF = { autorise: true, modifie_le: '2026-10-29T09:00:00+00:00' }

const config: AppConfig = {
  engines: ['whisper', 'mistral'], default_engine: 'whisper', default_project: 'Non classé',
  default_project_id: 'P1', whisper_model: 'w', llm_model: 'l', max_upload_mb: 2048,
  supported_extensions: ['.wav'], diarization_available: true, llm_available: true,
  llm_providers: ['mlx', 'mistral', 'openai', 'anthropic'], local_providers: ['mlx'], default_llm_provider: 'mlx',
  llm_ready: { mlx: true, mistral: true, openai: true, anthropic: true },
  sensitivity_levels: ['ultra_confidential', 'confidential', 'standard'], default_sensitivity: 'standard',
} as AppConfig

beforeEach(() => {
  vi.mocked(getCloudUltra).mockReset().mockResolvedValue(INACTIF)
  vi.mocked(setCloudUltra).mockReset()
  vi.mocked(getConfig).mockResolvedValue(config)
  vi.mocked(makeReport).mockReset()
  vi.mocked(getReglagesTraitement).mockReset().mockResolvedValue({ reglages: null })
  vi.mocked(lancerCompteRendu).mockReset().mockResolvedValue({ job_id: 'j1' })
})

// Une réunion dont le transcript est ultra-confidentiel, ouverte comme la personne l'ouvre.
const transcriptUltra = {
  name: 'rachat.md', id: 'rachat-id', meeting_id: 'M1', project_id: 'P1', title: 'rachat', date: '2026-11-05', duration: '00:10:00',
  language: 'fr', engine: 'whisper', source: 'rachat.wav', project: 'Équipe', diarization: false, profiled: false, n_speakers: null,
  type: null, source_transcript: null, source_document_id: null, warnings: 0, size: 1, modified: 1,
} as LibraryItem
const reunionUltra: Reunion = {
  cle: 'M1', source: 'rachat.wav', titre: 'rachat', date: '2026-11-05', duree: '00:10:00', langue: 'fr', projet: 'Équipe', nSpeakers: null,
  avertissements: 0, transcripts: [transcriptUltra], comptesRendus: [], retenu: transcriptUltra,
}
const retenu = (fournisseur: string) => ({
  reglages: { fournisseur, langue: '', resultats: { compte_rendu: true, titre_resume: true, locuteurs: false } } })

async function ouvrirLePanneauDuCompteRendu(user: ReturnType<typeof userEvent.setup>) {
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'rachat.md', meta: { sensitivity: 'ultra_confidential' }, sensitivity_effective: 'ultra_confidential',
    body: 'Texte', content: '# Rachat',
  } as never)
  render(<LangProvider initiale="fr"><MeetingView reunion={reunionUltra} onBack={vi.fn()} /></LangProvider>, { wrapper: avecConfirmation })
  await screen.findByText('Texte')
  await waitFor(() => expect(getCloudUltra).toHaveBeenCalled())
  await new Promise((fin) => setTimeout(fin, 0))
  await user.click(screen.getByRole('button', { name: 'Rédiger le compte rendu' }))
}

// ---------- Le panneau ----------

describe.each([
  ['fr', 'Confidentialité', 'Autoriser le cloud pour mon ultra-confidentiel', 'Oui, autoriser', 'Annuler',
    'Ne plus autoriser le cloud', /ne se partage toujours pas/, /Cloud non autorisé/],
  ['en', 'Privacy', 'Allow the cloud for my ultra-confidential documents', 'Yes, allow', 'Cancel',
    'Stop allowing the cloud', /still cannot be shared/, /Cloud not allowed/],
] as const)('Panneau Confidentialité (%s)', (lang, titre, activer, oui, annuler, desactiver, partage, inactif) => {
  it('dit ce que le réglage ouvre et n’ouvre pas, et demande confirmation pour l’activer', async () => {
    const user = userEvent.setup()
    vi.mocked(setCloudUltra).mockResolvedValue(ACTIF)
    render(<LangProvider initiale={lang}><ConfidentialitePanel /></LangProvider>, { wrapper: avecConfirmation })

    expect(screen.getByRole('heading', { name: titre })).toBeInTheDocument()
    expect(screen.getByText(partage)).toBeInTheDocument()
    expect(await screen.findByText(inactif)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: activer }))
    expect(setCloudUltra).not.toHaveBeenCalled()                 // rien sans confirmation
    await user.click(screen.getByRole('button', { name: annuler }))
    expect(setCloudUltra).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: activer }))
    await user.click(screen.getByRole('button', { name: oui }))
    expect(setCloudUltra).toHaveBeenCalledWith(true)
    expect(await screen.findByRole('button', { name: desactiver })).toBeInTheDocument()
  })

  it('désactive sans confirmation', async () => {
    const user = userEvent.setup()
    vi.mocked(getCloudUltra).mockResolvedValue(ACTIF)
    vi.mocked(setCloudUltra).mockResolvedValue(INACTIF)
    render(<LangProvider initiale={lang}><ConfidentialitePanel /></LangProvider>, { wrapper: avecConfirmation })

    await user.click(await screen.findByRole('button', { name: desactiver }))

    expect(setCloudUltra).toHaveBeenCalledTimes(1)
    expect(setCloudUltra).toHaveBeenCalledWith(false)
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: activer })).toBeInTheDocument()
  })
})

it('le bouton « Confidentialité » existe pour tout compte connecté, et Échap ferme', async () => {
  const user = userEvent.setup()
  vi.mocked(getEtatAuth).mockResolvedValue({
    installation_requise: false, connecte: true, utilisateur: { identifiant: 'alice', admin: false },
  })
  render(<LangProvider><AuthGate><BoutonConfidentialite /></AuthGate></LangProvider>, { wrapper: avecConfirmation })

  await user.click(await screen.findByRole('button', { name: 'Confidentialité' }))
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(await screen.findByText(/Cloud non autorisé/)).toBeInTheDocument()

  await user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

// ---------- Les écrans qui proposent un fournisseur ----------

function deposerUltra(container: HTMLElement) {
  fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement,
                   { target: { files: [new File(['a'], 'rachat.wav', { type: 'audio/wav' })] } })
}

it('Transcrire : le cloud n’est proposé pour l’ultra-confidentiel que si le réglage est actif', async () => {
  const user = userEvent.setup()
  vi.mocked(getCloudUltra).mockResolvedValue(ACTIF)
  const { container, unmount } = render(
    <LangProvider initiale="fr"><TranscribePanel config={config} onCompleted={vi.fn()} /></LangProvider>, { wrapper: avecConfirmation })
  deposerUltra(container)
  await user.selectOptions(screen.getByRole('combobox', { name: 'Sensibilité du document' }), 'ultra_confidential')
  // « En ligne » s'ouvre, puis on regarde si un fournisseur en ligne (hors MLX) peut être choisi.
  const enLigne = async () => {
    const bascule = screen.getByRole('button', { name: /^En ligne/ })
    if (bascule.getAttribute('aria-expanded') !== 'true') await user.click(bascule)
    return screen.getAllByRole('radio').filter((r) => !/MLX/.test(r.closest('label')?.textContent ?? ''))
  }
  await waitFor(async () => { for (const r of await enLigne()) expect(r).not.toBeDisabled() })
  expect(screen.getByText(/vous autorisez le cloud/)).toBeInTheDocument()
  unmount()

  // Réglage inactif : le même écran ne le propose pas.
  vi.mocked(getCloudUltra).mockResolvedValue(INACTIF)
  const second = render(
    <LangProvider initiale="fr"><TranscribePanel config={config} onCompleted={vi.fn()} /></LangProvider>, { wrapper: avecConfirmation })
  deposerUltra(second.container)
  await user.selectOptions(screen.getByRole('combobox', { name: 'Sensibilité du document' }), 'ultra_confidential')
  const enLigneRadios = await enLigne()
  expect(enLigneRadios.length).toBeGreaterThan(0)
  for (const r of enLigneRadios) expect(r).toBeDisabled()
})

it('Compte-rendu : avec le réglage actif, le fournisseur choisi n’est plus remplacé par le local', async () => {
  const user = userEvent.setup()
  vi.mocked(getCloudUltra).mockResolvedValue(ACTIF)
  vi.mocked(getReglagesTraitement).mockResolvedValue(retenu('openai'))
  await ouvrirLePanneauDuCompteRendu(user)

  // Le propriétaire autorise le cloud : le fournisseur retenu est présélectionné et part tel quel.
  expect(await screen.findByRole('radio', { name: /OpenAI/ })).toBeChecked()
  await user.click(screen.getByRole('button', { name: 'Rédiger ce compte rendu' }))
  await confirmerLaFenetre(user)                                                   // un fournisseur en ligne demande une confirmation avant l'envoi
  await waitFor(() => expect(lancerCompteRendu).toHaveBeenCalledWith('rachat.md', 'P1', 'openai'))
  aucuneFenetreDeConfirmation()                                                    // une seule demande, déjà traitée
  expect(makeReport).not.toHaveBeenCalled()
})

it('Compte-rendu : un modèle gratuit reste refusé pour l’ultra-confidentiel, même avec le réglage actif', async () => {
  const user = userEvent.setup()
  vi.mocked(getCloudUltra).mockResolvedValue(ACTIF)
  vi.mocked(getConfig).mockResolvedValue({
    ...config, llm_etats: {
      mlx: { lieu: 'local', etat: 'pret' }, openai: { lieu: 'en_ligne', etat: 'pret' },
      openrouter: { lieu: 'en_ligne', etat: 'gratuit' },
    },
  } as AppConfig)
  vi.mocked(getReglagesTraitement).mockResolvedValue(retenu('openrouter'))
  await ouvrirLePanneauDuCompteRendu(user)

  const gratuit = await screen.findByRole('radio', { name: /OpenRouter/ })
  expect(gratuit).toBeDisabled()
  expect(gratuit).not.toBeChecked()                                  // le modèle gratuit retenu n'est pas présélectionné
  expect(screen.getByRole('radio', { name: /OpenAI/ })).toBeEnabled()
  expect(screen.getByRole('radio', { name: /MLX/ })).toBeChecked()
  await user.click(screen.getByRole('button', { name: 'Rédiger ce compte rendu' }))
  await waitFor(() => expect(lancerCompteRendu).toHaveBeenCalledWith('rachat.md', 'P1', 'mlx'))
})

describe.each([
  ['fr', 'Réunion ultra-confidentielle : seuls les fournisseurs locaux sont proposés.'],
  ['en', 'Ultra-confidential meeting: only local providers are offered.'],
] as const)('Faits (%s)', (lang, note) => {
  it('avec le réglage actif, la relance propose aussi le cloud, sans la note « local seulement »', async () => {
    vi.mocked(getCloudUltra).mockResolvedValue(ACTIF)
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [] })
    render(<LangProvider initiale={lang}>
      <EclFaits meetingId="M1" viewScope="P1" onSource={() => {}} ultra />
    </LangProvider>, { wrapper: avecConfirmation })
    await waitFor(async () => {
      const options = (await screen.findAllByRole('option')) as HTMLOptionElement[]
      expect(options.map((o) => o.value)).toEqual(['mlx', 'mistral', 'openai', 'anthropic'])
    })
    expect(screen.queryByText(note)).not.toBeInTheDocument()
  })
})

it('un réglage illisible vaut inactif : l’écran ne propose que le local', async () => {
  vi.mocked(getCloudUltra).mockRejectedValue(new Error('Connexion requise.'))
  vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [] })
  render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="P1" onSource={() => {}} ultra /></LangProvider>, { wrapper: avecConfirmation })
  const options = (await screen.findAllByRole('option')) as HTMLOptionElement[]
  expect(options.map((o) => o.value)).toEqual(['mlx'])
})
