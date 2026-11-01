/**
 * Langue de l'interface : détection navigateur + surcharge manuelle persistée.
 *
 * Pas de bibliothèque : le mécanisme tient en une lecture de catalogue et une
 * interpolation `{param}`. Une dépendance de plus se paierait au premier audit
 * des dépendances non déclarées, pour un service que 60 lignes rendent.
 *
 * Persistance : même convention que `llmProvider.ts` — une clé `memoglyph.*`
 * dans `localStorage`, entourée d'un `try/catch` parce qu'en navigation privée
 * l'accès LÈVE au lieu de renvoyer `null`.
 *
 * Règle de conception : ce qui DÉCRIT un comportement
 * reste dérivé de l'état, en TypeScript. Le catalogue ne contient jamais une
 * phrase par combinaison d'état ; la partie variable passe par des PARAMÈTRES.
 * Ajouter un moteur ne doit ajouter aucune clé.
 */
import { ecrireCle, lireCle } from './stockage'
import { CATALOGUES } from './messages'
import type { MessageKey } from './messages'

export type Lang = 'fr' | 'en'
export const LANGS: readonly Lang[] = ['fr', 'en']

const STORAGE_KEY = 'lang'

function isLang(value: string | null | undefined): value is Lang {
  return value === 'fr' || value === 'en'
}

/** Le choix explicite de l'utilisateur, ou `null` s'il n'a jamais tranché. */
export function getLangOverride(): Lang | null {
  try {
    const stored = lireCle(STORAGE_KEY)
    return isLang(stored) ? stored : null
  } catch {
    // localStorage indisponible (mode privé, quota) : aucune surcharge connue.
    return null
  }
}

export function setLangOverride(lang: Lang): void {
  try {
    ecrireCle(STORAGE_KEY, lang)
  } catch {
    /* stockage indisponible : le choix ne persiste pas, sans casser le flux. */
  }
}

function navigatorPreferences(): readonly string[] {
  if (typeof navigator === 'undefined') return []
  return navigator.languages?.length ? navigator.languages : [navigator.language]
}

/**
 * Première langue connue dans les préférences du navigateur. `fr-CA` compte
 * comme `fr` : on compare la sous-étiquette primaire, jamais l'étiquette
 * entière. Défaut `fr`, la langue du corpus et de l'auteur.
 */
export function detectLang(preferences: readonly string[] = navigatorPreferences()): Lang {
  for (const tag of preferences) {
    const primaire = tag.toLowerCase().split('-')[0]
    if (isLang(primaire)) return primaire
  }
  return 'fr'
}

/** Le choix explicite prime toujours sur la détection. */
export function currentLang(): Lang {
  return getLangOverride() ?? detectLang()
}

/**
 * Un paramètre manquant laisse `{nom}` VISIBLE dans le texte rendu. Un trou
 * silencieux passerait la revue comme les tests ; un `{moteur}` à l'écran, non.
 */
export function translate(
  lang: Lang,
  key: MessageKey,
  params?: Readonly<Record<string, string | number>>,
): string {
  const modele = CATALOGUES[lang][key]
  if (!params) return modele
  return modele.replace(/\{(\w+)\}/g, (entier, nom: string) =>
    nom in params ? String(params[nom]) : entier,
  )
}

export type Traduire = (
  key: MessageKey,
  params?: Readonly<Record<string, string | number>>,
) => string

/**
 * Choisit la clé singulier/pluriel d'un compteur. Règle FRANÇAISE, la même que
 * le panneau Recherche : seul `n > 1` prend le pluriel — « 0
 * document » et « 1 document » restent au singulier. On garde une seule règle
 * pour tout le catalogue plutôt que d'en introduire une seconde par langue.
 */
export function pluralKey(base: string, n: number): MessageKey {
  return `${base}.${n > 1 ? 'other' : 'one'}` as MessageKey
}

/**
 * Une étape de traitement telle que le backend la publie.
 *
 * `code` peut être `null` : un message d'exception ou le texte d'une
 * dépendance n'a pas de code stable. `message` est le repli ANGLAIS produit
 * par le backend, jamais vide.
 */
export interface EtapeJob {
  code: string | null
  params: Readonly<Record<string, string | number>>
  message: string
}

/**
 * Traduit une étape, ou rend le repli du backend.
 *
 * **Le repli est le point important.** Un backend plus récent peut émettre un
 * code que cette interface ne connaît pas : afficher un vide, ou la clé brute,
 * ferait passer une mise à jour de backend pour une panne. On montre alors
 * l'anglais du backend — dégradé, mais juste et lisible.
 */
export function traduireEtape(lang: Lang, etape: EtapeJob): string {
  if (!etape.code) return etape.message
  const key = `job.${etape.code}` as MessageKey
  if (!(key in CATALOGUES[lang])) return etape.message
  return translate(lang, key, etape.params)
}
