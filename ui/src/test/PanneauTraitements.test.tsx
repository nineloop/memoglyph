import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AnnulationError } from '../api'
import type { ListeTraitements, Traitement } from '../api'
import { IndicateurTraitements } from '../components/IndicateurTraitements'
import { LangProvider } from '../components/LangProvider'
import { PanneauTraitements } from '../components/PanneauTraitements'

const T = (extra: Partial<Traitement> = {}): Traitement => ({
  id: 'j1', nature: 'transcription', statut: 'running', en_attente: false, progression: 0.4,
  titre: 'visio du 2 novembre', projet_id: 'P', projet_nom: 'Réunions AMI', etape: 2, etapes: 5,
  message_code: null, message_params: {}, debut: 1000, meeting: null,
  erreur: null, erreur_code: null, erreur_params: {}, ...extra,
})
const L = (traitements: Traitement[], actifs = 0, attente = 0): ListeTraitements =>
  ({ traitements, actifs, en_attente: attente })
const MAINTENANT = 1000 + 4 * 60 + 10

beforeEach(() => { localStorage.clear(); localStorage.setItem('memoglyph.lang', 'fr') })

describe('l\'indicateur de la barre haute', () => {
  it('est caché quand rien ne tourne', () => {
    const { container } = render(<LangProvider><IndicateurTraitements liste={L([], 0, 0)} ouvert={false} surBasculer={() => {}} /></LangProvider>)
    expect(container).toBeEmptyDOMElement()
    const { container: c2 } = render(<LangProvider><IndicateurTraitements liste={null} ouvert={false} surBasculer={() => {}} /></LangProvider>)
    expect(c2).toBeEmptyDOMElement()
  })

  it('porte les comptes dans son nom accessible et dans son texte', () => {
    render(<LangProvider><IndicateurTraitements liste={L([T()], 1, 2)} ouvert={false} surBasculer={() => {}} /></LangProvider>)
    const bouton = screen.getByRole('button', { name: /traitements : 1 en cours, 2 en attente/i })
    expect(bouton).toHaveAttribute('aria-expanded', 'false')
    expect(bouton).toHaveTextContent('1 en cours')
    expect(bouton).toHaveTextContent('2 en attente')
  })

  it('a une pastille réduite avec le nombre total pour le mobile', () => {
    render(<LangProvider><IndicateurTraitements liste={L([T()], 1, 2)} ouvert={false} surBasculer={() => {}} /></LangProvider>)
    expect(document.querySelector('.indicateur-pastille')).toHaveTextContent('3')
  })

  it('un clic ouvre ou ferme le panneau', async () => {
    const basculer = vi.fn()
    render(<LangProvider><IndicateurTraitements liste={L([T()], 1, 0)} ouvert={false} surBasculer={basculer} /></LangProvider>)
    await userEvent.click(screen.getByRole('button', { name: /traitements/i }))
    expect(basculer).toHaveBeenCalledTimes(1)
  })

  it('reste affiché tant que le panneau est ouvert, même quand tout est fini', () => {
    render(<LangProvider><IndicateurTraitements liste={L([T({ statut: 'done' })], 0, 0)} ouvert surBasculer={() => {}} /></LangProvider>)
    expect(screen.getByRole('button', { name: /traitements/i })).toHaveAttribute('aria-expanded', 'true')
  })
})

describe('le panneau des traitements', () => {
  const monter = (liste: ListeTraitements | null, props: Partial<React.ComponentProps<typeof PanneauTraitements>> = {}) => {
    const surFermer = vi.fn(), surOuvrir = vi.fn()
    render(<LangProvider><PanneauTraitements liste={liste} maintenant={MAINTENANT} surFermer={surFermer}
                                             surOuvrirReunion={surOuvrir} {...props} /></LangProvider>)
    return { surFermer, surOuvrir }
  }

  it('montre un traitement en cours : état avec libellé, titre, projet · étape n sur N · durée, progression', () => {
    monter(L([T()], 1, 0))
    const item = screen.getByRole('listitem')
    expect(within(item).getByText('En cours')).toBeInTheDocument()          // un libellé, pas seulement une couleur
    expect(within(item).getByText('visio du 2 novembre')).toBeInTheDocument()
    expect(item).toHaveTextContent('Réunions AMI')
    expect(item).toHaveTextContent('étape 2 sur 5')
    expect(item).toHaveTextContent('depuis 4 min')                 // en cours : « depuis »
    const barre = within(item).getByRole('progressbar')
    expect(barre).toHaveAttribute('aria-valuenow', '40')
    expect(barre).toHaveAccessibleName(/progression : 40 %/i)
  })

  it('un traitement en attente le dit, sans étape ni progression trompeuses', () => {
    monter(L([T({ statut: 'pending', en_attente: true, etape: null, progression: 0 })], 0, 1))
    const item = screen.getByRole('listitem')
    expect(within(item).getByText('En attente')).toBeInTheDocument()
    expect(item).toHaveTextContent(/un autre traitement est en cours/i)
    expect(within(item).queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('un traitement terminé dit « il y a 2 min » (comme l\'anglais « 2 min ago »), pas « depuis »', () => {
    monter(L([T({ statut: 'done', progression: 1, debut: 1000 + 4 * 60 + 10 - 2 * 60 })]))
    const item = screen.getByRole('listitem')
    expect(item).toHaveTextContent('il y a 2 min')
    expect(item).not.toHaveTextContent('depuis')
  })

  it('un traitement terminé renvoie à SA réunion', async () => {
    const { surOuvrir } = monter(L([T({ statut: 'done', progression: 1,
      meeting: { name: 'a.md', meeting_id: 'M1', project_id: 'P' } })]))
    const lien = screen.getByRole('link', { name: /ouvrir la réunion « visio du 2 novembre »/i })
    expect(lien).toHaveAttribute('href', expect.stringContaining('reunion=M1'))
    await userEvent.click(lien)
    expect(surOuvrir).toHaveBeenCalledWith({ name: 'a.md', meeting_id: 'M1', project_id: 'P' })
    expect(screen.getByText('Terminé')).toBeInTheDocument()
  })

  it('un échec se dit en une phrase ; le détail brut est à part, sous un titre traduit', () => {
    monter(L([T({ statut: 'error', erreur: 'Traceback: boum' })]))
    const item = screen.getByRole('listitem')
    expect(within(item).getByText('Échec')).toBeInTheDocument()
    expect(item.querySelector('.traitement-echec-bloc p')).toHaveTextContent('Le traitement a échoué.')
    expect(item.querySelector('.traitement-echec-bloc p')).not.toHaveTextContent('Traceback')
    const detail = item.querySelector('details.traitement-echec-detail') as HTMLElement
    expect(within(detail).getByText('Détail technique')).toBeInTheDocument()
    expect(detail).toHaveTextContent('Traceback: boum')
    expect(detail).not.toHaveAttribute('open')                       // replié par défaut
  })

  it('un échec avec son code est dit dans la langue de l\'interface, jamais par le texte brut français', () => {
    localStorage.setItem('memoglyph.lang', 'en')
    render(<LangProvider initiale="en"><PanneauTraitements maintenant={MAINTENANT} surFermer={() => {}} surOuvrirReunion={() => {}}
      liste={L([T({ statut: 'error', erreur: 'Extraction vide : aucune unité retenue sur 3 appel(s) au modèle.',
        erreur_code: 'step.error.emptyExtraction' })])} /></LangProvider>)
    const item = screen.getByRole('listitem')
    expect(item.querySelector('.traitement-echec-bloc p')).toHaveTextContent('The model kept nothing from this meeting. Try another model.')
    expect(item.querySelector('.traitement-echec-bloc p')).not.toHaveTextContent(/Extraction vide/)
    expect(within(item).getByText('Technical detail')).toBeInTheDocument()   // le texte brut reste, à part et étiqueté
  })

  it('la nature figure sur la carte : deux cartes au même titre se distinguent', () => {
    monter(L([T({ id: 'a', nature: 'compte-rendu', etape: 1, etapes: 3 }), T({ id: 'b', nature: 'transcription', etape: 1, etapes: 5 })], 2, 0))
    const [cr, tr] = screen.getAllByRole('listitem')
    expect(cr.querySelector('.traitement-details')).toHaveTextContent(/^Compte rendu · Réunions AMI · étape 1 sur 3/)
    expect(tr.querySelector('.traitement-details')).toHaveTextContent(/^Transcription · Réunions AMI · étape 1 sur 5/)
  })

  it('la nature s\'écrit en anglais, et la nature générique n\'apparaît pas', () => {
    localStorage.setItem('memoglyph.lang', 'en')
    render(<LangProvider initiale="en"><PanneauTraitements maintenant={MAINTENANT} surFermer={() => {}} surOuvrirReunion={() => {}}
      liste={L([T({ id: 'a', nature: 'compte-rendu', etape: 1, etapes: 3 }), T({ id: 'g', nature: 'traitement', etape: null, etapes: null })], 2, 0)} /></LangProvider>)
    const [cr, generique] = screen.getAllByRole('listitem')
    expect(cr.querySelector('.traitement-details')).toHaveTextContent(/^Report · Réunions AMI · step 1 of 3/)
    expect(generique.querySelector('.traitement-details')).not.toHaveTextContent(/Job/)
  })

  it('un échec « trop d\'attente » est traduit à partir de son CODE, pas du texte brut', () => {
    monter(L([T({ statut: 'error', erreur: 'Ressources occupées : un autre traitement est en cours.',
      erreur_code: 'step.error.waitTooLong', erreur_params: { label: 'traitement' } })]))
    expect(screen.getByRole('listitem')).toHaveTextContent(/trop d’attente/i)
  })

  it('traduit le nom du projet de la réception (pas « Non classé » en anglais)', () => {
    localStorage.setItem('memoglyph.lang', 'en')
    render(<LangProvider initiale="en"><PanneauTraitements liste={L([T({ projet_nom: 'Non classé' })], 1, 0)}
      maintenant={MAINTENANT} surFermer={() => {}} surOuvrirReunion={() => {}} /></LangProvider>)
    expect(screen.getByRole('listitem')).toHaveTextContent('Unclassified')
    expect(screen.getByRole('listitem')).not.toHaveTextContent('Non classé')
  })

  it('« Tout voir » déplie l\'historique au-delà des terminés récents', async () => {
    const finis = [1, 2, 3, 4, 5, 6, 7].map((i) => T({ id: `f${i}`, titre: `fini ${i}`, statut: 'done', progression: 1 }))
    monter(L(finis))
    expect(screen.getAllByRole('listitem')).toHaveLength(5)
    await userEvent.click(screen.getByRole('button', { name: /tout voir \(7\)/i }))
    expect(screen.getAllByRole('listitem')).toHaveLength(7)
    await userEvent.click(screen.getByRole('button', { name: /voir moins/i }))
    expect(screen.getAllByRole('listitem')).toHaveLength(5)
  })

  it('n\'offre pas « Tout voir » quand il n\'y a rien de plus', () => {
    monter(L([T({ statut: 'done' })]))
    expect(screen.queryByRole('button', { name: /tout voir/i })).not.toBeInTheDocument()
  })

  it('rappelle que les traitements continuent si l\'on change d\'écran ou ferme l\'onglet', () => {
    monter(L([T()], 1, 0))
    expect(screen.getByText(/continuent si vous changez d’écran ou fermez l’onglet/i)).toBeInTheDocument()
  })

  it('dit qu\'il n\'y a rien, plutôt que de rester vide', () => {
    monter(L([]))
    expect(screen.getByText(/aucun traitement pour le moment/i)).toBeInTheDocument()
  })

  it('une nature sans titre (faits, voix, archives) se nomme par sa nature', () => {
    monter(L([T({ nature: 'faits', titre: '' })], 1, 0))
    expect(screen.getByText('Extraction des faits')).toBeInTheDocument()
  })

  it('se ferme par son bouton et par Échap', async () => {
    const { surFermer } = monter(L([T()], 1, 0))
    await userEvent.click(screen.getByRole('button', { name: /fermer les traitements/i }))
    await userEvent.keyboard('{Escape}')
    expect(surFermer).toHaveBeenCalledTimes(2)
  })

  it('Échap appartient à une fenêtre modale ouverte par-dessus (Partager, Appareils…)', async () => {
    const { surFermer } = monter(L([T()], 1, 0))
    const modale = document.createElement('div')
    modale.setAttribute('role', 'dialog')
    modale.setAttribute('aria-modal', 'true')
    document.body.appendChild(modale)
    await userEvent.keyboard('{Escape}')
    expect(surFermer).not.toHaveBeenCalled()
    modale.remove()
    await userEvent.keyboard('{Escape}')
    expect(surFermer).toHaveBeenCalledTimes(1)
  })

  it('continuité : un panneau réouvert liste le traitement toujours en cours', () => {
    const { unmount } = render(<LangProvider><PanneauTraitements liste={L([T()], 1, 0)} maintenant={MAINTENANT}
      surFermer={() => {}} surOuvrirReunion={() => {}} /></LangProvider>)
    unmount()
    monter(L([T()], 1, 0))
    expect(screen.getByText('visio du 2 novembre')).toBeInTheDocument()
  })
})


// ---------- annuler un traitement en attente de place ----------

describe('annuler un traitement en attente', () => {
  const attente = (extra: Partial<Traitement> = {}) => T({
    id: 'w1', statut: 'running', en_attente: true, etape: null, progression: 0, titre: 'Comité', ...extra })
  const annule = (extra: Partial<Traitement> = {}) => T({
    id: 'x1', statut: 'error', titre: 'Ancien envoi', progression: 0, etape: null, erreur: 'Traitement annulé.',
    erreur_code: 'step.error.cancelled', ...extra })

  function monterAnnulable(liste: ListeTraitements, annuler = vi.fn().mockResolvedValue(undefined), langue: 'fr' | 'en' = 'fr') {
    localStorage.setItem('memoglyph.lang', langue)
    const rafraichir = vi.fn()
    const rendu = render(
      <LangProvider initiale={langue}>
        <IndicateurTraitements liste={liste} ouvert surBasculer={() => {}} />
        <PanneauTraitements liste={liste} maintenant={MAINTENANT} surFermer={() => {}} surOuvrirReunion={() => {}}
                            annuler={annuler} rafraichir={rafraichir} />
      </LangProvider>)
    return { annuler, rafraichir, ...rendu }
  }

  it('le bouton « Annuler » n\'existe QUE pour un traitement qui attend sa place (running + en attente)', () => {
    monterAnnulable(L([
      attente({ id: 'a1', titre: 'Attend' }),
      T({ id: 'p1', titre: 'En réception', statut: 'pending', en_attente: true }),
      T({ id: 'r1', titre: 'Tourne' }),
      T({ id: 'd1', titre: 'Fini', statut: 'done', progression: 1 }),
      annule({ id: 'x2', titre: 'Déjà annulé' }),
    ], 1, 2))
    expect(screen.getAllByRole('button', { name: /^Annuler le traitement/ })).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Annuler le traitement « Attend »' })).toBeInTheDocument()
  })

  it('porte un nom accessible avec le titre, et une cible tactile de classe dédiée', () => {
    monterAnnulable(L([attente()], 0, 1))
    const bouton = screen.getByRole('button', { name: 'Annuler le traitement « Comité »' })
    expect(bouton).toHaveTextContent('Annuler')
    expect(bouton).toHaveClass('traitement-annuler')
  })

  it('une confirmation REFUSÉE ne change rien : ni appel, ni rafraîchissement, ni message', async () => {
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(false)
    try {
      const { annuler, rafraichir } = monterAnnulable(L([attente()], 0, 1))
      await userEvent.click(screen.getByRole('button', { name: /^Annuler le traitement/ }))
      expect(confirmer).toHaveBeenCalledWith('Annuler ce traitement ? Le fichier envoyé ne sera pas traité.')
      expect(annuler).not.toHaveBeenCalled()
      expect(rafraichir).not.toHaveBeenCalled()
      expect(screen.getByRole('status')).toBeEmptyDOMElement()
    } finally { confirmer.mockRestore() }
  })

  it('une confirmation ACCEPTÉE annule, relève la liste tout de suite et le dit dans une zone d\'état', async () => {
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(true)
    try {
      const { annuler, rafraichir } = monterAnnulable(L([attente()], 0, 1))
      await userEvent.click(screen.getByRole('button', { name: /^Annuler le traitement/ }))
      expect(annuler).toHaveBeenCalledWith('w1')
      await waitFor(() => expect(rafraichir).toHaveBeenCalledTimes(1))
      expect(screen.getByRole('status')).toHaveTextContent('Traitement annulé.')
    } finally { confirmer.mockRestore() }
  })

  it('un double clic n\'envoie qu\'une annulation (le bouton se désactive pendant la requête)', async () => {
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(true)
    try {
      let finir: () => void = () => {}
      const annuler = vi.fn(() => new Promise<void>((resolve) => { finir = resolve }))
      monterAnnulable(L([attente()], 0, 1), annuler)
      const bouton = screen.getByRole('button', { name: /^Annuler le traitement/ })
      await userEvent.click(bouton)
      await userEvent.click(bouton)
      expect(annuler).toHaveBeenCalledTimes(1)
      expect(bouton).toBeDisabled()
      await act(async () => { finir() })
    } finally { confirmer.mockRestore() }
  })

  it('après l\'annulation, le focus passe à la ligne suivante ; sans autre ligne, au déclencheur du panneau', async () => {
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(true)
    try {
      const deux = monterAnnulable(L([attente({ id: 'w1', titre: 'Premier' }), attente({ id: 'w2', titre: 'Second' })], 0, 2))
      await userEvent.click(screen.getByRole('button', { name: 'Annuler le traitement « Premier »' }))
      await waitFor(() => expect(deux.rafraichir).toHaveBeenCalled())
      expect(document.activeElement).toBe(document.querySelector('[data-traitement="w2"]'))
      deux.unmount()

      monterAnnulable(L([attente({ id: 'w1', titre: 'Seul' })], 0, 1))
      await userEvent.click(screen.getByRole('button', { name: 'Annuler le traitement « Seul »' }))
      await waitFor(() => expect(document.activeElement).toBe(document.querySelector('.indicateur-traitements')))
    } finally { confirmer.mockRestore() }
  })

  it('perdre la course (le traitement a obtenu sa place) se dit en une phrase, sans texte brut, et relève la liste', async () => {
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(true)
    try {
      const annuler = vi.fn().mockRejectedValue(new AnnulationError('impossible', 'running'))
      const { rafraichir } = monterAnnulable(L([attente()], 0, 1), annuler)
      await userEvent.click(screen.getByRole('button', { name: /^Annuler le traitement/ }))
      await waitFor(() => expect(rafraichir).toHaveBeenCalledTimes(1))
      const etat = screen.getByRole('status')
      expect(etat).toHaveTextContent('Ce traitement ne peut plus être annulé.')
      expect(etat).not.toHaveTextContent(/HTTP|409|running|impossible/)
    } finally { confirmer.mockRestore() }
  })

  it('un traitement déjà oublié du serveur (404) se dit aussi, et la liste est relevée', async () => {
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(true)
    try {
      const annuler = vi.fn().mockRejectedValue(new AnnulationError('introuvable', ''))
      const { rafraichir } = monterAnnulable(L([attente()], 0, 1), annuler)
      await userEvent.click(screen.getByRole('button', { name: /^Annuler le traitement/ }))
      await waitFor(() => expect(rafraichir).toHaveBeenCalledTimes(1))
      expect(screen.getByRole('status')).toHaveTextContent('Ce traitement n’existe plus.')
    } finally { confirmer.mockRestore() }
  })

  it('une autre panne dit « L\'annulation a échoué », sans montrer l\'erreur brute', async () => {
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(true)
    try {
      const annuler = vi.fn().mockRejectedValue(new Error('HTTP 500: boum'))
      monterAnnulable(L([attente()], 0, 1), annuler)
      await userEvent.click(screen.getByRole('button', { name: /^Annuler le traitement/ }))
      await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('L’annulation a échoué. Réessayez.'))
      expect(screen.getByRole('status')).not.toHaveTextContent(/HTTP|boum/)
    } finally { confirmer.mockRestore() }
  })

  it('une ligne annulée : pastille neutre « Annulé », dans « Récemment terminés », sans lien ni détail technique', () => {
    monterAnnulable(L([annule({ meeting: { name: 'a.md', meeting_id: 'M1', project_id: 'P' } })], 0, 0))
    const ligne = screen.getByText('Ancien envoi').closest('li') as HTMLElement
    // Rangée sous « Terminés récemment », jamais parmi les traitements actifs.
    expect(ligne.closest('ul')?.previousElementSibling).toHaveTextContent('Terminés récemment')
    expect(screen.queryByRole('heading', { name: 'En cours et en attente' })).not.toBeInTheDocument()
    const pastille = within(ligne).getByText('Annulé')
    expect(pastille.closest('.traitement-etat')).toHaveClass('etat-annule')
    expect(within(ligne).queryByRole('link')).not.toBeInTheDocument()                   // pas d'« Ouvrir la réunion »
    expect(ligne.querySelector('details, .traitement-echec-bloc')).toBeNull()           // ni bloc d'échec ni détail technique
    expect(ligne).not.toHaveTextContent(/Échec|Détail technique|Traitement annulé/)     // ni vocabulaire d'échec
    expect(within(ligne).queryByRole('button', { name: /Annuler/ })).not.toBeInTheDocument()
  })

  it('en anglais : tous les libellés nouveaux', async () => {
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(true)
    try {
      const annuler = vi.fn().mockResolvedValue(undefined)
      monterAnnulable(L([attente({ titre: 'Board' }), annule({ id: 'x9', titre: 'Old upload' })], 0, 1), annuler, 'en')
      expect(within(screen.getByText('Old upload').closest('li') as HTMLElement).getByText('Cancelled')).toBeInTheDocument()
      const bouton = screen.getByRole('button', { name: 'Cancel job “Board”' })
      expect(bouton).toHaveTextContent('Cancel')
      await userEvent.click(bouton)
      expect(confirmer).toHaveBeenCalledWith('Cancel this job? The file you sent will not be processed.')
      await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Job cancelled.'))
    } finally { confirmer.mockRestore() }
  })
})

describe('le focus après une annulation qui n\'a pas eu lieu', () => {
  const attente = (extra: Partial<Traitement> = {}) => T({
    id: 'w1', statut: 'running', en_attente: true, etape: null, progression: 0, titre: 'Comité', ...extra })

  it.each([
    ['la course perdue (409)', new AnnulationError('impossible', 'running')],
    ['un traitement oublié (404)', new AnnulationError('introuvable', '')],
    ['une panne', new Error('HTTP 500: boum')],
  ])('après %s, le focus va au TITRE du panneau, puis y reste quand la liste relevée retire le bouton', async (_nom, refus) => {
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(true)
    try {
      const annuler = vi.fn().mockRejectedValue(refus)
      const surFermer = () => {}, surOuvrir = () => {}
      const monter = (liste: ListeTraitements) => (
        <LangProvider initiale="fr"><PanneauTraitements liste={liste} maintenant={MAINTENANT} surFermer={surFermer}
          surOuvrirReunion={surOuvrir} annuler={annuler} rafraichir={() => {}} /></LangProvider>)
      const { rerender } = render(monter(L([attente()], 0, 1)))

      await userEvent.click(screen.getByRole('button', { name: /^Annuler le traitement/ }))

      const titre = screen.getByRole('heading', { name: 'Traitements', level: 2 })
      await waitFor(() => expect(document.activeElement).toBe(titre))
      // La liste relevée : le traitement a obtenu sa place, le bouton n'existe plus ; le focus ne tombe pas sur le document.
      rerender(monter(L([attente({ en_attente: false, progression: 0.1, etape: 1 })], 1, 0)))
      expect(screen.queryByRole('button', { name: /^Annuler le traitement/ })).not.toBeInTheDocument()
      expect(document.activeElement).toBe(titre)
      expect(document.activeElement).not.toBe(document.body)
    } finally { confirmer.mockRestore() }
  })
})

