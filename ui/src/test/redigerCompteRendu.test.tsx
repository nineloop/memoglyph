/**
 * Un seul chemin pour produire un compte rendu : « Rédiger le compte rendu » (aucun compte rendu) ou « Régénérer », qui ouvrent le
 * panneau de choix du fournisseur, suivi comme un traitement. L'ancien bouton « Compte rendu » du transcript, qui appelait la route
 * synchrone sans choix ni suivi, n'existe plus.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AppConfig, LibraryItem, ListeTraitements, Traitement } from '../api'
import { getCloudUltra, getConfig, getReglagesTraitement, getTranscript, lancerCompteRendu } from '../api'
import { LangProvider } from '../components/LangProvider'
import { MeetingView } from '../components/MeetingView'
import { RegenererCompteRendu } from '../components/RegenererCompteRendu'
import { TranscriptReader } from '../components/TranscriptReader'
import type { Reunion } from '../lib/meetings'
import { TraitementsContext } from '../lib/traitementsContext'
import { avecConfirmation } from './confirmation'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual, getTranscript: vi.fn(), getConfig: vi.fn(), lancerCompteRendu: vi.fn(),
    getReglagesTraitement: vi.fn(), getCloudUltra: vi.fn(),
  }
})

const SANS_HORODATAGE = 'Transcrit sans horodatage : un compte rendu ne pourrait rien citer.'

const CONFIG = {
  llm_etats: { mlx: { lieu: 'local', etat: 'pret' }, groq: { lieu: 'en_ligne', etat: 'pret' } },
  local_providers: ['mlx'], default_llm_provider: 'mlx', llm_models: { mlx: 'Qwen3-8B', groq: 'un-modele' },
} as unknown as AppConfig

function document(name: string, type: string | null = null): LibraryItem {
  return {
    name, id: `${name}-id`, meeting_id: 'meeting-id', project_id: 'project-id', title: 'point-prix', date: '2026-07-21',
    duration: '00:42:00', language: 'fr', engine: 'whisper', source: 'Point prix.mp4', project: 'Non classé',
    diarization: true, profiled: true, n_speakers: 2, type, source_transcript: type === 'report' ? 'transcript-a.md' : null,
    source_document_id: null, warnings: 0, size: 100, modified: 1_700_000_000,
  } as LibraryItem
}

const transcript = document('transcript-a.md')
const compteRendu = document('report-a.md', 'report')
const sansCompteRendu: Reunion = {
  cle: 'meeting-id', source: 'Point prix.mp4', titre: 'point-prix', date: '2026-07-21', duree: '00:42:00', langue: 'fr',
  projet: 'Non classé', nSpeakers: 2, avertissements: 0, transcripts: [transcript], comptesRendus: [], retenu: transcript,
}
const avecCompteRendu: Reunion = { ...sansCompteRendu, comptesRendus: [compteRendu] }

function detail(name: string, horodatage: boolean) {
  return {
    name, meta: name.startsWith('report') ? { type: 'report', genre: 'generique' } : { pipeline: { stt: { timestamps: horodatage } } },
    body: name.startsWith('report') ? '## Décisions\n\n- Prix fixé' : 'Transcript', content: '[00:00:01] Bonjour', has_audio: false,
  }
}

function traitement(sur: Partial<Traitement>): Traitement {
  return { id: 'j1', nature: 'compte-rendu', statut: 'running', en_attente: false, progression: 0.3, titre: 'Point prix',
    projet_id: 'P1', projet_nom: 'Équipe', etape: 1, etapes: 3, message_code: 'step.report.extract', message_params: {},
    debut: 0, meeting: null, erreur: null, erreur_code: null, erreur_params: {}, ...sur }
}

function monter(reunion: Reunion, options: { liste?: ListeTraitements | null; onChanged?: () => void } = {}) {
  const rendu = (liste: ListeTraitements | null) => (
    <LangProvider initiale="fr">
      <TraitementsContext.Provider value={{ liste, rafraichir: vi.fn(), suivre: vi.fn() }}>
        <MeetingView reunion={reunion} onBack={vi.fn()} onChanged={options.onChanged} />
      </TraitementsContext.Provider>
    </LangProvider>
  )
  const vue = render(rendu(options.liste ?? null), { wrapper: avecConfirmation })
  return { ...vue, avec: (liste: ListeTraitements | null) => vue.rerender(rendu(liste)) }
}

let horodate = true
beforeEach(() => {
  horodate = true
  vi.mocked(getConfig).mockReset().mockResolvedValue(CONFIG)
  vi.mocked(getCloudUltra).mockReset().mockResolvedValue({ autorise: false, modifie_le: null } as never)
  vi.mocked(getReglagesTraitement).mockReset().mockResolvedValue({ reglages: null })
  vi.mocked(lancerCompteRendu).mockReset().mockResolvedValue({ job_id: 'j1' })
  vi.mocked(getTranscript).mockReset().mockImplementation((async (name: string) => detail(name, horodate)) as never)
})

describe('l\'état vide du compte rendu', () => {
  it('montre le choix du fournisseur tout de suite, sans renvoyer vers le transcript', async () => {
    monter(sansCompteRendu)
    await userEvent.click(screen.getByRole('tab', { name: /Compte rendu/ }))          // sans compte rendu, la réunion s'ouvre sur le transcript

    expect(await screen.findByRole('radio', { name: /MLX/ })).toBeChecked()
    expect(screen.getByText('Aucun compte rendu')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ouvrir le transcript' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Fermer' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('le lancement part du transcript de la réunion, puis la réunion se met à jour à la fin du traitement', async () => {
    const onChanged = vi.fn()
    const vue = monter(sansCompteRendu, { onChanged })
    await userEvent.click(screen.getByRole('tab', { name: /Compte rendu/ }))
    await screen.findByRole('radio', { name: /MLX/ })

    await userEvent.click(screen.getByRole('button', { name: 'Rédiger ce compte rendu' }))

    expect(lancerCompteRendu).toHaveBeenCalledWith('transcript-a.md', 'project-id', 'mlx')
    vue.avec({ actifs: 0, en_attente: 0, traitements: [traitement({ statut: 'done', meeting: { name: 'report-a.md' } as never })] })
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
  })
})

describe('l\'onglet Transcript', () => {
  it('n\'a plus de bouton « Compte rendu » : « Rédiger le compte rendu » quand il n\'y en a pas, « Régénérer » sinon', async () => {
    const { unmount } = monter(sansCompteRendu)
    await userEvent.click(screen.getByRole('tab', { name: /Transcript/ }))
    const panneau = await screen.findByRole('tabpanel', { name: 'Transcript' })
    expect(within(panneau).getByRole('button', { name: 'Rédiger le compte rendu' })).toBeEnabled()
    expect(within(panneau).queryByRole('button', { name: /^Compte rendu$/ })).not.toBeInTheDocument()
    unmount()

    monter(avecCompteRendu)
    await userEvent.click(screen.getByRole('tab', { name: /Transcript/ }))
    const avec = await screen.findByRole('tabpanel', { name: 'Transcript' })
    expect(within(avec).getByRole('button', { name: 'Régénérer' })).toBeEnabled()
    expect(within(avec).queryByRole('button', { name: /^Compte rendu$/ })).not.toBeInTheDocument()
  })

  it('« Rédiger » ouvre le panneau de l\'onglet Compte rendu : un seul chemin', async () => {
    monter(avecCompteRendu)
    await userEvent.click(screen.getByRole('tab', { name: /Transcript/ }))
    const panneau = await screen.findByRole('tabpanel', { name: 'Transcript' })

    await userEvent.click(within(panneau).getByRole('button', { name: 'Régénérer' }))

    expect(screen.getByRole('tab', { name: /Compte rendu/ })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByRole('radio', { name: /MLX/ })).toBeInTheDocument()
    expect(lancerCompteRendu).not.toHaveBeenCalled()                       // ouvrir ne lance rien
  })

  it('ni l\'Assistant, ni la Recherche, ni les Doublons ne proposent de compte rendu : le lecteur seul n\'a pas de bouton', async () => {
    render(<LangProvider initiale="fr"><TranscriptReader name="transcript-a.md" documentScope="all" onBack={vi.fn()} /></LangProvider>, { wrapper: avecConfirmation })
    await screen.findByText('transcript-a.md')
    expect(screen.queryByRole('button', { name: /compte rendu|Régénérer/i })).not.toBeInTheDocument()
  })
})

describe('l\'ouverture du panneau', () => {
  const rendu = (demande: number, liste: ListeTraitements | null = null) => (
    <LangProvider initiale="fr">
      <TraitementsContext.Provider value={{ liste, rafraichir: vi.fn(), suivre: vi.fn() }}>
        <RegenererCompteRendu name="transcript-a.md" documentScope="all" demandeOuverture={demande} />
      </TraitementsContext.Provider>
    </LangProvider>
  )

  it('une demande déjà non nulle au montage n\'ouvre pas le panneau (la barre d\'outils qui apparaît après une rédaction)', () => {
    render(rendu(3), { wrapper: avecConfirmation })
    expect(screen.getByRole('button', { name: 'Régénérer' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('un CHANGEMENT de la demande l\'ouvre', async () => {
    const { rerender } = render(rendu(3), { wrapper: avecConfirmation })
    rerender(rendu(4))
    expect(await screen.findByRole('radio', { name: /MLX/ })).toBeInTheDocument()
  })
})

describe('la sensibilité connue après l\'ouverture du panneau', () => {
  const rendu = (ultra: boolean) => (
    <LangProvider initiale="fr">
      <TraitementsContext.Provider value={{ liste: null, rafraichir: vi.fn(), suivre: vi.fn() }}>
        <RegenererCompteRendu name="transcript-a.md" documentScope="all" ultra={ultra} ouvertInitial />
      </TraitementsContext.Provider>
    </LangProvider>
  )

  it('un fournisseur en ligne déjà coché est remplacé par le local quand la réunion se révèle ultra-confidentielle', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue({
      reglages: { fournisseur: 'groq', langue: '', resultats: { compte_rendu: true, titre_resume: true, locuteurs: false } } })
    const { rerender } = render(rendu(false), { wrapper: avecConfirmation })
    expect(await screen.findByRole('radio', { name: /Groq/ })).toBeChecked()          // la configuration est chargée, la sensibilité pas encore connue

    rerender(rendu(true))

    await waitFor(() => expect(screen.getByRole('radio', { name: /MLX/ })).toBeChecked())
    expect(screen.getByRole('radio', { name: /Groq/ })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /Groq/ })).not.toBeChecked()
  })
})

describe('un fournisseur retenu mais pas configuré, dans « Régénérer »', () => {
  const rendu = () => (
    <LangProvider initiale="fr">
      <TraitementsContext.Provider value={{ liste: null, rafraichir: vi.fn(), suivre: vi.fn() }}>
        <RegenererCompteRendu name="transcript-a.md" documentScope="all" ouvertInitial />
      </TraitementsContext.Provider>
    </LangProvider>
  )
  const retenu = (fournisseur: string) => ({
    reglages: { fournisseur, langue: '', resultats: { compte_rendu: true, titre_resume: true, locuteurs: false } } })

  it('dit en une ligne que le traitement se fera en Local, avec le nom du fournisseur ; le local est présélectionné', async () => {
    vi.mocked(getConfig).mockResolvedValue({
      ...CONFIG, llm_etats: { ...CONFIG.llm_etats, openai: { lieu: 'en_ligne', etat: 'non_configure' } } } as unknown as AppConfig)
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenu('openai'))
    render(rendu(), { wrapper: avecConfirmation })

    expect(await screen.findByText('OpenAI, retenu pour votre compte, n’est pas configuré : le traitement se fera en Local.')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /MLX/ })).toBeChecked()
  })

  it('pas de ligne quand le fournisseur retenu est prêt ou que rien n\'est retenu', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenu('groq'))
    const { unmount } = render(rendu(), { wrapper: avecConfirmation })
    expect(await screen.findByRole('radio', { name: /Groq/ })).toBeChecked()
    expect(screen.queryByText(/retenu pour votre compte/)).not.toBeInTheDocument()
    unmount()
    vi.mocked(getReglagesTraitement).mockResolvedValue({ reglages: null })
    render(rendu(), { wrapper: avecConfirmation })
    await screen.findByRole('radio', { name: /MLX/ })
    expect(screen.queryByText(/retenu pour votre compte/)).not.toBeInTheDocument()
  })
})

describe('un transcript sans horodatage : un compte rendu n\'y citerait rien', () => {
  beforeEach(() => { horodate = false })

  it('le bouton de l\'onglet Transcript est désactivé, avec l\'explication', async () => {
    monter(avecCompteRendu)
    await userEvent.click(screen.getByRole('tab', { name: /Transcript/ }))
    const panneau = await screen.findByRole('tabpanel', { name: 'Transcript' })
    const bouton = within(panneau).getByRole('button', { name: 'Régénérer' })
    await waitFor(() => expect(bouton).toBeDisabled())
    expect(bouton).toHaveAttribute('title', SANS_HORODATAGE)
  })

  it('l\'entrée de l\'état vide est désactivée, avec la même explication, et le panneau ne s\'ouvre pas', async () => {
    monter(sansCompteRendu)
    await userEvent.click(screen.getByRole('tab', { name: /Compte rendu/ }))
    const bouton = await screen.findByRole('button', { name: 'Rédiger le compte rendu' })
    await waitFor(() => expect(bouton).toBeDisabled())
    expect(bouton).toHaveAttribute('title', SANS_HORODATAGE)
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
  })

  it('l\'entrée de la barre d\'outils du compte rendu est désactivée, avec la même explication', async () => {
    monter(avecCompteRendu)
    const bouton = await screen.findByRole('button', { name: 'Régénérer' })
    await waitFor(() => expect(bouton).toBeDisabled())
    expect(bouton).toHaveAttribute('title', SANS_HORODATAGE)
  })

  it('avec horodatages, les trois entrées sont actives', async () => {
    horodate = true
    monter(avecCompteRendu)
    const bouton = await screen.findByRole('button', { name: 'Régénérer' })
    await waitFor(() => expect(getTranscript).toHaveBeenCalled())
    expect(bouton).toBeEnabled()
  })
})
