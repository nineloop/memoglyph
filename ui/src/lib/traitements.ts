/**
 * Les traitements du compte, vus de n'importe quel écran (chantier 003, tranche B).
 *
 * Tout ce qui se calcule vit ici, SANS React : états affichés, comptes, durée « depuis 4 min », et la
 * relève de `GET /api/jobs` (3 s quand quelque chose tourne, 15 s au repos, suspendue onglet masqué).
 * Un traitement lancé reste visible jusqu'à sa fin, quel que soit l'écran où l'on se trouve.
 */
import type { ListeTraitements, Traitement } from '../api'

export const INTERVALLE_ACTIF_MS = 3000
export const INTERVALLE_REPOS_MS = 15000
/** Combien de traitements terminés le panneau montre avant « Tout voir ». */
export const TERMINES_VISIBLES = 5

export type EtatAffiche = 'en_attente' | 'en_cours' | 'termine' | 'echec'

/** « En attente » = le créneau n'est pas obtenu (`en_attente`) ou le traitement n'a pas démarré (`pending`). */
export function etatAffiche(t: Traitement): EtatAffiche {
  if (t.statut === 'done') return 'termine'
  if (t.statut === 'error') return 'echec'
  if (t.statut === 'pending' || t.en_attente) return 'en_attente'
  return 'en_cours'
}

export function comptes(liste: ListeTraitements | null): { enCours: number; enAttente: number; total: number } {
  const enCours = liste?.actifs ?? 0
  const enAttente = liste?.en_attente ?? 0
  return { enCours, enAttente, total: enCours + enAttente }
}

/** Les actifs d'abord, puis les terminés récents ; `tout` rend tout l'historique reçu. */
export function separer(liste: ListeTraitements | null, tout: boolean): { actifs: Traitement[]; termines: Traitement[] } {
  const toutes = liste?.traitements ?? []
  const actifs = toutes.filter((t) => etatAffiche(t) === 'en_cours' || etatAffiche(t) === 'en_attente')
  const termines = toutes.filter((t) => etatAffiche(t) === 'termine' || etatAffiche(t) === 'echec')
  return { actifs, termines: tout ? termines : termines.slice(0, TERMINES_VISIBLES) }
}

/** La durée écoulée, en une unité ; jamais négative (une horloge en retard donne « à l'instant »). */
export function valeurDepuis(debut: number, maintenant: number): { unite: 'now' | 'min' | 'h'; n: number } {
  const s = Math.max(0, maintenant - debut)
  if (s < 60) return { unite: 'now', n: 0 }
  if (s < 3600) return { unite: 'min', n: Math.floor(s / 60) }
  return { unite: 'h', n: Math.floor(s / 3600) }
}

export function intervalleReleve(liste: ListeTraitements | null): number {
  return comptes(liste).total > 0 ? INTERVALLE_ACTIF_MS : INTERVALLE_REPOS_MS
}

type OptionsReleve = {
  recuperer: () => Promise<ListeTraitements>
  surListe: (liste: ListeTraitements) => void
  /** Injectable pour les tests ; le navigateur par défaut. */
  doc?: Document
}

/**
 * Relève `recuperer` à son rythme et rend `{ arreter, rafraichir }`.
 *
 * - **Une seule boucle** : une relève en cours n'en lance pas une seconde, `rafraichir()` compris. Sous
 *   StrictMode (développement) l'effet démarre, s'arrête puis redémarre : `arreter()` ne laisse AUCUNE
 *   minuterie, sans quoi la relève serait doublée.
 * - **Suspendue onglet masqué** : aucune requête ; au retour (`visibilitychange`), relève immédiate.
 * - Une erreur réseau garde la dernière liste et réessaie au rythme du repos.
 */
export function demarrerReleve({ recuperer, surListe, doc = document }: OptionsReleve): {
  arreter: () => void
  rafraichir: () => void
} {
  let minuteur: ReturnType<typeof setTimeout> | undefined
  let arrete = false
  let enCours = false
  let derniere: ListeTraitements | null = null

  const visible = () => doc.visibilityState !== 'hidden'
  const effacer = () => { if (minuteur !== undefined) { clearTimeout(minuteur); minuteur = undefined } }

  async function tour(): Promise<void> {
    effacer()
    if (arrete || enCours || !visible()) return
    enCours = true
    let delai = INTERVALLE_REPOS_MS
    try {
      derniere = await recuperer()
      if (!arrete) surListe(derniere)
      delai = intervalleReleve(derniere)
    } catch {
      /* la dernière liste reste affichée ; on réessaie au rythme du repos */
    } finally {
      enCours = false
    }
    if (arrete || !visible()) return
    effacer()
    minuteur = setTimeout(() => { void tour() }, delai)
  }

  const auRetour = () => { if (visible()) void tour() }
  doc.addEventListener('visibilitychange', auRetour)
  void tour()

  return {
    arreter() {
      arrete = true
      effacer()
      doc.removeEventListener('visibilitychange', auRetour)
    },
    rafraichir() { void tour() },
  }
}
