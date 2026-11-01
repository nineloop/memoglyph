/**
 * L'état de navigation dans l'URL — retour arrière, rechargement, partage.
 *
 * Pourquoi : rien n'était dans l'URL, le bouton retour du
 * navigateur quittait l'application, un rechargement ramenait à Transcrire, et
 * envoyer un lien vers une réunion était impossible.
 *
 * ## Pas de routeur
 *
 * `ui/package.json` ne dépend que de `react` et `react-dom`. `history.pushState`
 * et `popstate` suffisent pour ce que l'application demande, et une dépendance
 * de routage se paierait au premier audit des dépendances — pour un service que
 * quarante lignes rendent.
 *
 * ## Query string, pas de segments de chemin
 *
 * `/?vue=search&q=climat` et non `/search/climat`. Rien ne sert `index.html`
 * hors du serveur de développement Vite : un rechargement sur `/library/…`
 * donnerait un 404. La query string est servie par n'importe quel hébergeur
 * statique sans configuration.
 *
 * ## La validation n'est pas cosmétique
 *
 * Les blocs de rendu d'`App.tsx` sont gardés par `tab === '…'`. Une valeur hors
 * liste afficherait un `<main>` VIDE pendant que l'en-tête, lui, retombe déjà
 * sur le premier onglet — l'écran serait incohérent, pas seulement faux. Une
 * `vue` inconnue retombe donc sur `transcribe`, un `onglet` inconnu sur rien.
 */

export const VUES = [
  'transcribe', 'library', 'search', 'ask', 'voices',
  'usage', 'dedup', 'trash', 'maintenance',
] as const
export type Vue = (typeof VUES)[number]

// La SEULE liste des onglets d'une réunion : MeetingView l'importe. Deux listes avaient
// divergé, et l'onglet Faits ne survivait pas à un lien ni à un rechargement.
export const ONGLETS_REUNION = ['cr', 'transcript', 'faits', 'versions', 'info'] as const
export type OngletReunion = (typeof ONGLETS_REUNION)[number]

export type EtatUrl = {
  /**
   * ABSENTE quand l'URL n'en porte pas — et c'est une information, pas un
   * détail de typage.
   *
   * Tant que « pas de `vue` » et « `vue=transcribe` » rendaient la même chose,
   * aucun appelant ne pouvait choisir une vue d'ouverture sans risquer
   * d'écraser un lien partagé. Une valeur PRÉSENTE mais hors liste reste, elle,
   * un choix explicite mal orthographié : on la ramène au défaut plutôt que de
   * la traiter comme une absence, sinon une URL malformée deviendrait un
   * laissez-passer vers la préférence locale.
   */
  vue?: Vue
  projet?: string
  reunion?: string
  onglet?: OngletReunion
  q?: string
}

/** Ordre FIXE, pour que deux états égaux produisent la même chaîne. */
const ORDRE: (keyof EtatUrl)[] = ['vue', 'projet', 'reunion', 'onglet', 'q']

// La sentinelle du sélecteur : elle déclenche la création d'un projet et ne
// désigne aucune portée. Elle ne doit JAMAIS atteindre l'URL — un rechargement
// rouvrirait la boîte de dialogue de création.
const SENTINELLE_NOUVEAU = '__new__'

export function lireUrl(search: string = location.search): EtatUrl {
  const params = new URLSearchParams(search)
  const vue = params.get('vue')
  const onglet = params.get('onglet')
  const projet = params.get('projet')

  const etat: EtatUrl = {}
  // `null` — et lui seul — signifie que la clé manque.
  if (vue !== null) {
    etat.vue = (VUES as readonly string[]).includes(vue) ? (vue as Vue) : 'transcribe'
  }
  if (projet && projet !== SENTINELLE_NOUVEAU) etat.projet = projet
  const reunion = params.get('reunion')
  if (reunion) etat.reunion = reunion
  if ((ONGLETS_REUNION as readonly string[]).includes(onglet ?? '')) {
    etat.onglet = onglet as OngletReunion
  }
  const q = params.get('q')
  if (q) etat.q = q
  return etat
}

export function ecrireUrl(etat: EtatUrl): string {
  const params = new URLSearchParams()
  for (const cle of ORDRE) {
    const valeur = etat[cle]
    if (!valeur) continue
    if (cle === 'projet' && valeur === SENTINELLE_NOUVEAU) continue
    // Un onglet de réunion n'a de sens qu'avec une réunion.
    if (cle === 'onglet' && !etat.reunion) continue
    params.set(cle, String(valeur))
  }
  const chaine = params.toString()
  return chaine ? `?${chaine}` : location.pathname
}
