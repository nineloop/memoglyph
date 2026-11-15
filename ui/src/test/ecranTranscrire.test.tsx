/**
 * L'écran Transcrire en deux zones : les réglages du traitement à gauche, le dépôt, « Ranger dans » + « Lancer » puis le suivi à droite.
 * Sur petit écran, les réglages s'ouvrent en fenêtre. Le fournisseur se choisit par le sélecteur partagé, dans le panneau des réglages.
 *
 * jsdom ne calcule pas la mise en page : on vérifie ici la STRUCTURE (qui est dans quelle zone, dans quel ordre, sur quelle ligne)
 * et le comportement de la fenêtre ; les mesures du bouton viennent de la démo (captures).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AppConfig } from '../api'
import { annulerTraitement, getReglagesTraitement, streamJob, submitTranscribe } from '../api'
import { LangProvider } from '../components/LangProvider'
import { TranscribePanel } from '../components/TranscribePanel'
import { aucuneFenetreDeConfirmation, avecConfirmation, confirmer as confirmerLaFenetre, fenetreDeConfirmation, refuser } from './confirmation'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, submitTranscribe: vi.fn(), streamJob: vi.fn(), getReglagesTraitement: vi.fn(), annulerTraitement: vi.fn() }
})

const CONFIG: AppConfig = {
  engines: ['whisper', 'mistral'], default_engine: 'whisper', default_project: 'Non classé',
  default_project_id: '01KY243SVP5GA31F6KSBYXEAHR', whisper_model: 'w', llm_model: 'l', max_upload_mb: 2048,
  supported_extensions: ['.mp3'], diarization_available: false, llm_available: true,
  llm_providers: ['mistral', 'mlx', 'openai', 'groq'], local_providers: ['mlx'], default_llm_provider: 'mlx',
  llm_ready: { mlx: true, mistral: true, openai: true, groq: false },
  llm_etats: {
    mlx: { lieu: 'local', etat: 'pret' }, mistral: { lieu: 'en_ligne', etat: 'pret' },
    openai: { lieu: 'en_ligne', etat: 'pret' }, groq: { lieu: 'en_ligne', etat: 'non_configure' },
  },
  llm_models: { mlx: 'qwen', mistral: 'mistral-chat', openai: 'gpt-test' },
  sensitivity_levels: ['ultra_confidential', 'confidential', 'standard'], default_sensitivity: 'standard',
} as AppConfig

function monter(options: { fichier?: boolean; langue?: 'fr' | 'en'; traitements?: never } = {}) {
  const { fichier = false, langue = 'fr', traitements } = options
  const rendu = render(
    <LangProvider initiale={langue}><TranscribePanel config={CONFIG} onCompleted={vi.fn()} traitements={traitements ?? null} /></LangProvider>, { wrapper: avecConfirmation })
  if (fichier) choisir(rendu.container)
  return rendu
}

function choisir(conteneur: HTMLElement, nom = 'visio.mp3') {
  fireEvent.change(conteneur.querySelector('input[type="file"]') as HTMLInputElement,
                   { target: { files: [new File(['audio'], nom, { type: 'audio/mpeg' })] } })
}

const reglages = () => screen.getByRole('region', { name: 'Réglages du traitement' })

beforeEach(() => {
  vi.mocked(submitTranscribe).mockReset().mockResolvedValue('job-1')
  vi.mocked(streamJob).mockReset().mockImplementation(() => vi.fn())
  vi.mocked(getReglagesTraitement).mockReset().mockResolvedValue({ reglages: null })
  vi.mocked(annulerTraitement).mockReset().mockResolvedValue(undefined)
})

describe('deux zones', () => {
  it('les réglages du traitement sont à gauche, avant le dépôt, et déjà là sans fichier choisi', () => {
    monter()
    const panneau = reglages()
    const depot = screen.getByRole('button', { name: /Choisir un fichier/ })
    expect(panneau.compareDocumentPosition(depot) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(panneau).getByRole('combobox', { name: 'Langue parlée' })).toBeInTheDocument()
    expect(within(panneau).getByRole('combobox', { name: 'Sensibilité du document' })).toBeInTheDocument()
    expect(within(panneau).getByRole('group', { name: 'Où traiter' })).toBeInTheDocument()
  })

  it('le dépôt, « Ranger dans » et le suivi sont hors du panneau des réglages', () => {
    monter({ fichier: true })
    const panneau = reglages()
    expect(panneau).not.toContainElement(screen.getByRole('button', { name: /visio\.mp3/ }))
    expect(panneau).not.toContainElement(screen.getByText('Ranger dans'))
    expect(panneau).not.toContainElement(document.getElementById('process-title'))
  })

  it('« Lancer la transcription » est sur la même ligne que « Ranger dans », et seulement une fois le fichier choisi', () => {
    monter()
    expect(screen.queryByRole('button', { name: 'Lancer la transcription' })).not.toBeInTheDocument()
    choisir(document.body)
    const bouton = screen.getByRole('button', { name: 'Lancer la transcription' })
    const ligne = bouton.closest('[data-ligne="envoi"]') as HTMLElement
    expect(ligne).not.toBeNull()
    expect(within(ligne).getByText('Ranger dans')).toBeInTheDocument()
    expect(reglages()).not.toContainElement(bouton)
  })

  it('le suivi du traitement apparaît sur le même écran dès le lancement', async () => {
    monter({ fichier: true })
    await userEvent.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
    await waitFor(() => expect(streamJob).toHaveBeenCalled())
    expect(screen.getByRole('heading', { name: 'Traitement', level: 2 })).toBeInTheDocument()
    expect(reglages()).not.toContainElement(document.getElementById('process-title'))
  })

  it('à droite, le suivi vient juste après le dépôt, avant la note du compagnon', () => {
    monter({ fichier: true })
    const depot = screen.getByRole('button', { name: /visio\.mp3/ })
    const suivi = document.getElementById('process-title') as HTMLElement
    const compagnon = document.getElementById('companion-title') as HTMLElement
    expect(depot.compareDocumentPosition(suivi) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(suivi.compareDocumentPosition(compagnon) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('« Identifier les locuteurs » est grisé avec une aide d\'une ligne quand l\'installation manque, en français et en anglais', () => {
    const fr = monter({ fichier: true })
    expect(screen.getByRole('checkbox', { name: /Identifier les locuteurs/ })).toBeDisabled()
    expect(screen.getByText('Jeton Hugging Face requis')).toBeInTheDocument()
    fr.unmount()
    monter({ fichier: true, langue: 'en' })
    expect(screen.getByRole('checkbox', { name: /Identify the speakers/ })).toBeDisabled()
    expect(screen.getByText('Hugging Face token required')).toBeInTheDocument()
  })

  it('la case du profilage dit ce qu\'elle produit (résumé et thèmes), sans promettre un titre ni nommer le modèle, en français et en anglais', () => {
    const fr = monter({ fichier: true })
    const caseFr = screen.getByRole('checkbox', { name: /Résumé et thèmes/ })
    expect(caseFr.closest('label')).toHaveTextContent('Type de réunion, résumé, thèmes et profils des locuteurs')
    expect(caseFr.closest('label')).not.toHaveTextContent('qwen')
    expect(screen.queryByRole('checkbox', { name: /Titre et résumé/ })).toBeNull()
    fr.unmount()
    monter({ fichier: true, langue: 'en' })
    const caseEn = screen.getByRole('checkbox', { name: /Summary and themes/ })
    expect(caseEn.closest('label')).toHaveTextContent('Meeting type, summary, themes and speaker profiles')
    expect(caseEn.closest('label')).not.toHaveTextContent('qwen')
    expect(screen.queryByRole('checkbox', { name: /Title and summary/ })).toBeNull()
  })

  it('« Vous pouvez quitter cette page » est dit tant qu\'un traitement tourne, et pas au repos', async () => {
    monter({ fichier: true })
    expect(screen.queryByText(/Vous pouvez quitter cette page/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
    await waitFor(() => expect(streamJob).toHaveBeenCalled())
    expect(screen.getByText('Vous pouvez quitter cette page : le traitement continue.')).toBeInTheDocument()
  })

  it('en anglais, la même phrase', async () => {
    monter({ fichier: true, langue: 'en' })
    await userEvent.click(screen.getByRole('button', { name: 'Start the transcription' }))
    await waitFor(() => expect(streamJob).toHaveBeenCalled())
    expect(screen.getByText('You can leave this page: processing continues.')).toBeInTheDocument()
  })

  it('« Annuler » est proposé tant que le traitement attend sa place, et seulement alors', async () => {
    const liste = (enAttente: boolean) => ({
      actifs: 0, en_attente: enAttente ? 1 : 0,
      traitements: [{ id: 'job-1', nature: 'transcription', statut: 'running', en_attente: enAttente, progression: 0,
        titre: 'visio', projet_id: null, projet_nom: '', etape: null, etapes: null, message_code: null,
        message_params: {}, debut: 0, meeting: null, erreur: null, erreur_code: null, erreur_params: {} }],
    })
    const rendu = monter({ fichier: true })
    await userEvent.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
    await waitFor(() => expect(streamJob).toHaveBeenCalled())
    const suivi = () => document.getElementById('process-title')!.closest('aside') as HTMLElement
    expect(within(suivi()).queryByRole('button', { name: 'Annuler' })).not.toBeInTheDocument()

    rendu.rerender(<LangProvider initiale="fr"><TranscribePanel config={CONFIG} onCompleted={vi.fn()} traitements={liste(true) as never} /></LangProvider>)
    const annuler = within(suivi()).getByRole('button', { name: 'Annuler' })
    await userEvent.click(annuler)
    expect(await fenetreDeConfirmation()).toHaveAccessibleName('Annuler ce traitement ?')
    await confirmerLaFenetre(userEvent)
    await waitFor(() => expect(annulerTraitement).toHaveBeenCalledWith('job-1'))

    rendu.rerender(<LangProvider initiale="fr"><TranscribePanel config={CONFIG} onCompleted={vi.fn()} traitements={liste(false) as never} /></LangProvider>)
    expect(within(suivi()).queryByRole('button', { name: 'Annuler' })).not.toBeInTheDocument()
  })

  it('refuser la confirmation n’annule rien', async () => {
    const liste = {
      actifs: 0, en_attente: 1,
      traitements: [{ id: 'job-1', nature: 'transcription', statut: 'running', en_attente: true, progression: 0,
        titre: 'visio', projet_id: null, projet_nom: '', etape: null, etapes: null, message_code: null,
        message_params: {}, debut: 0, meeting: null, erreur: null, erreur_code: null, erreur_params: {} }],
    }
    const rendu = monter({ fichier: true })
    await userEvent.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
    await waitFor(() => expect(streamJob).toHaveBeenCalled())
    rendu.rerender(<LangProvider initiale="fr"><TranscribePanel config={CONFIG} onCompleted={vi.fn()} traitements={liste as never} /></LangProvider>)
    await userEvent.click(screen.getByRole('button', { name: 'Annuler' }))
    await refuser(userEvent)
    aucuneFenetreDeConfirmation()
    expect(annulerTraitement).not.toHaveBeenCalled()
  })
})

describe('le sélecteur de fournisseur dans le panneau', () => {
  it('sous le sélecteur, une ligne dit où partent les données : rien ne sort du serveur en Local, l\'audio et le texte chez Mistral', async () => {
    monter({ fichier: true })
    expect(screen.getByText('Rien ne sort du serveur Memoglyph.')).toBeInTheDocument()
    const groupe = within(reglages()).getByRole('group', { name: 'Où traiter' })
    await userEvent.click(within(groupe).getByRole('button', { name: /^En ligne/ }))
    await userEvent.click(within(groupe).getByRole('radio', { name: /Mistral/ }))
    expect(screen.queryByText('Rien ne sort du serveur Memoglyph.')).not.toBeInTheDocument()
    expect(screen.getByText('L’audio et le texte partent chez Mistral.')).toBeInTheDocument()
    await userEvent.click(within(groupe).getByRole('radio', { name: /OpenAI/ }))
    expect(screen.queryByText(/L’audio et le texte partent/)).not.toBeInTheDocument()   // OpenAI ne transcrit pas : l'autre ligne le dit
  })

  it('les mêmes lignes en anglais', async () => {
    monter({ fichier: true, langue: 'en' })
    expect(screen.getByText('Nothing leaves the Memoglyph server.')).toBeInTheDocument()
    const groupe = within(screen.getByRole('region', { name: 'Processing settings' })).getByRole('group', { name: 'Where to process' })
    await userEvent.click(within(groupe).getByRole('button', { name: /^Online/ }))
    await userEvent.click(within(groupe).getByRole('radio', { name: /Mistral/ }))
    expect(screen.getByText('Audio and text go to Mistral.')).toBeInTheDocument()
  })

  it('« Local » d’abord, « En ligne » replié, chaque fournisseur en ligne sous son vrai nom avec son état', async () => {
    monter({ fichier: true })
    const groupe = within(reglages()).getByRole('group', { name: 'Où traiter' })
    expect(within(groupe).getByRole('radio', { name: /MLX/ })).toBeChecked()
    expect(within(groupe).queryByRole('radio', { name: /OpenAI/ })).not.toBeInTheDocument()
    await userEvent.click(within(groupe).getByRole('button', { name: /En ligne/ }))
    expect(within(groupe).getByRole('radio', { name: /OpenAI/ })).toBeInTheDocument()
    expect(within(groupe).getByText('Non configuré')).toBeInTheDocument()                    // Groq : grisé, dit tel quel
    expect(within(groupe).queryByRole('radio', { name: /Groq/ })).not.toBeInTheDocument()
  })

  it('un document ultra-confidentiel grise « En ligne » avec « Local uniquement »', async () => {
    monter({ fichier: true })
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Sensibilité du document' }), 'ultra_confidential')
    const groupe = within(reglages()).getByRole('group', { name: 'Où traiter' })
    expect(within(groupe).getByText(/Local uniquement/)).toBeInTheDocument()
    await userEvent.click(within(groupe).getByRole('button', { name: /En ligne/ }))
    expect(within(groupe).getByRole('radio', { name: /OpenAI/ })).toBeDisabled()
  })

  it('choisir un fournisseur en ligne l’envoie avec la transcription restée locale (OpenAI ne transcrit pas)', async () => {
    monter({ fichier: true })
    const groupe = within(reglages()).getByRole('group', { name: 'Où traiter' })
    await userEvent.click(within(groupe).getByRole('button', { name: /En ligne/ }))
    await userEvent.click(within(groupe).getByRole('radio', { name: /OpenAI/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({ llm_provider: 'openai', engine: 'whisper' })
  })

  it('choisir Mistral transcrit aussi en ligne ; revenir à « Local » rend le moteur local', async () => {
    monter({ fichier: true })
    const groupe = within(reglages()).getByRole('group', { name: 'Où traiter' })
    await userEvent.click(within(groupe).getByRole('button', { name: /En ligne/ }))
    await userEvent.click(within(groupe).getByRole('radio', { name: /Mistral/ }))
    await userEvent.click(within(groupe).getByRole('radio', { name: /MLX/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({ llm_provider: 'mlx', engine: 'whisper' })
  })
})

describe('sur petit écran, les réglages s’ouvrent en fenêtre', () => {
  it('le bouton d’ouverture annonce l’état, la fenêtre n’est modale que tant qu’elle est ouverte', async () => {
    monter({ fichier: true })
    const ouvrir = screen.getByRole('button', { name: 'Réglages du traitement' })
    expect(ouvrir).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()               // fermée : une région ordinaire, pas un dialogue modal
    expect(reglages()).not.toHaveAttribute('aria-modal')                       // et rien ne le laisse entendre
    await userEvent.click(ouvrir)
    const fenetre = screen.getByRole('dialog', { name: 'Réglages du traitement' })
    expect(fenetre).toHaveAttribute('aria-modal', 'true')
    expect(ouvrir).toHaveAttribute('aria-expanded', 'true')
  })

  it('le focus entre dans la fenêtre, Échap la ferme, le focus revient au bouton qui l’a ouverte', async () => {
    monter({ fichier: true })
    const ouvrir = screen.getByRole('button', { name: 'Réglages du traitement' })
    await userEvent.click(ouvrir)
    const fenetre = screen.getByRole('dialog')
    await waitFor(() => expect(fenetre).toContainElement(document.activeElement as HTMLElement))
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(ouvrir).toHaveFocus()
    expect(ouvrir).toHaveAttribute('aria-expanded', 'false')
  })

  it('un bouton « Fermer » ferme aussi la fenêtre et rend le focus', async () => {
    monter({ fichier: true })
    const ouvrir = screen.getByRole('button', { name: 'Réglages du traitement' })
    await userEvent.click(ouvrir)
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Fermer les réglages' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(ouvrir).toHaveFocus()
  })

  it('Tab reste dans la fenêtre ouverte (le focus boucle)', async () => {
    monter({ fichier: true })
    await userEvent.click(screen.getByRole('button', { name: 'Réglages du traitement' }))
    const fenetre = screen.getByRole('dialog')
    for (let i = 0; i < 40; i++) {
      await userEvent.tab()
      expect(fenetre).toContainElement(document.activeElement as HTMLElement)
    }
  })

  it('les choix faits dans la fenêtre sont ceux de l’envoi, et la fenêtre fermée ne les perd pas', async () => {
    monter({ fichier: true })
    await userEvent.click(screen.getByRole('button', { name: 'Réglages du traitement' }))
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Langue parlée' }), 'es')
    await userEvent.keyboard('{Escape}')
    await userEvent.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({ language: 'es' })
  })

  it('en anglais, mêmes noms : « Processing settings », « Close the settings »', async () => {
    monter({ fichier: true, langue: 'en' })
    const ouvrir = screen.getByRole('button', { name: 'Processing settings' })
    await userEvent.click(ouvrir)
    const fenetre = screen.getByRole('dialog', { name: 'Processing settings' })
    expect(within(fenetre).getByRole('button', { name: 'Close the settings' })).toBeInTheDocument()
  })
})
