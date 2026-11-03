import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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

describe('l\'indicateur de la barre haute (US5)', () => {
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

describe('le panneau des traitements (US5)', () => {
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
