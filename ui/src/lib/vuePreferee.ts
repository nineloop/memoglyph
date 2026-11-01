/**
 * Où l'application s'ouvre quand l'URL ne le dit pas.
 *
 * Ouvrir toujours sur Transcrire coûte un clic à chaque reprise à quelqu'un qui
 * a déjà du contenu. On mémorise donc la dernière vue, et à défaut on regarde
 * si le corpus est vide.
 *
 * ## L'URL garde le dernier mot
 *
 * Ce module ne décide RIEN quand l'URL porte une `vue` : un lien partagé
 * `?vue=library&reunion=Y` doit ouvrir ce qu'il annonce, chez son destinataire
 * comme chez son auteur. C'est pourquoi `lireUrl` distingue désormais l'absence
 * du défaut — sans cette distinction, la préférence locale écraserait
 * silencieusement les liens reçus, un défaut pire que celui qu'on corrige.
 *
 * ## Seules les vues de TRAVAIL sont mémorisées
 *
 * Maintenance, Corbeille et Doublons sont des détours : on y va pour une
 * opération précise et on en revient. Rouvrir l'application sur la corbeille
 * parce qu'on y a restauré un document la veille serait déroutant.
 *
 * On reprend le patron de persistance de `llmProvider.ts` — `localStorage`
 * entouré d'une garde — plutôt que d'en inventer un second.
 */
import { ecrireCle, lireCle } from './stockage'
import type { Vue } from './urlState'

const STORAGE_KEY = 'vue'

/** Les vues où l'on travaille, les seules qui soient un point de départ. */
export const VUES_MEMORISABLES = [
  'transcribe', 'library', 'search', 'ask', 'voices',
] as const
export type VueMemorisable = (typeof VUES_MEMORISABLES)[number]

function estMemorisable(valeur: string | null): valeur is VueMemorisable {
  return (VUES_MEMORISABLES as readonly string[]).includes(valeur ?? '')
}

/**
 * La vue mémorisée, ou `null`.
 *
 * Une valeur illisible ou inconnue ne bloque rien : le stockage peut avoir été
 * écrit par une version antérieure — ou à la main — et refuser de démarrer
 * là-dessus serait absurde. On retombe alors sur la règle du corpus.
 */
export function lireVueMemorisee(): VueMemorisable | null {
  try {
    const brut = lireCle(STORAGE_KEY)
    return estMemorisable(brut) ? brut : null
  } catch {
    // localStorage indisponible (mode privé, quota) : aucune préférence.
    return null
  }
}

export function memoriserVue(vue: string): void {
  if (!estMemorisable(vue)) return
  try {
    ecrireCle(STORAGE_KEY, vue)
  } catch {
    /* stockage indisponible : la reprise ne persiste pas, sans casser le flux. */
  }
}

/**
 * La vue d'ouverture, dans l'ordre : l'URL, puis la mémoire, puis le corpus.
 *
 * `corpusVide` n'est connu qu'APRÈS le chargement des projets, alors que la vue
 * est choisie au montage. L'appelant évalue donc cette fonction deux fois — une
 * première avec `corpusVide` supposé vrai, puis une seconde quand la liste
 * arrive — et les deux premières branches, elles, tranchent dès le montage.
 */
export function vueDeDepart(
  vueUrl: Vue | undefined,
  memorisee: VueMemorisable | null,
  corpusVide: boolean,
): Vue {
  if (vueUrl) return vueUrl
  if (memorisee) return memorisee
  return corpusVide ? 'transcribe' : 'library'
}
