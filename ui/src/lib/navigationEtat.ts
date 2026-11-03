/**
 * L'état de navigation, SANS React : trois responsabilités que `viewScope` mélangeait.
 *
 * - **consulté** : le projet dont la Bibliothèque affiche les réunions (dans l'URL : `projet`) ;
 * - **destination** : le projet où se range la prochaine transcription (jamais dans l'URL) ;
 * - **panneau replié** : l'état du panneau des projets (jamais dans l'URL).
 *
 * L'invariant qui justifie ce module (SC-002) : changer l'un ne modifie jamais l'autre.
 * Le menu natif « Portée consultée » changeait de SENS selon l'écran ; ici la destination
 * se choisit dans Transcrire, la consultation dans le panneau ou le fil d'Ariane.
 *
 * La mémoire du navigateur passe par `stockage.ts` et ses appelants gardent leur `try/catch` :
 * le stockage peut manquer (navigation privée, quota) et rien ne doit en dépendre.
 */
import type { Project } from '../api'
import { ecrireCle, lireCle } from './stockage'

export type EtatNavigation = {
  /** Identifiant de projet, `all` ou `shared` (les partages reçus). */
  consulte: string
  /** Identifiant du projet de destination, ou `null` tant qu'aucun n'est choisi. */
  destination: string | null
}

export type ActionNavigation =
  | { type: 'consulter'; projet: string }
  | { type: 'destiner'; projet: string }

export function reduireNavigation(etat: EtatNavigation, action: ActionNavigation): EtatNavigation {
  if (action.type === 'consulter') {
    return etat.consulte === action.projet ? etat : { ...etat, consulte: action.projet }
  }
  return etat.destination === action.projet ? etat : { ...etat, destination: action.projet }
}

// ---------- Destination ----------

/** Les projets où l'on peut ranger : ceux du compte qui ont un identifiant. Les projets reçus n'y sont pas. */
export function destinationsPossibles(projets: Project[]): (Project & { id: string })[] {
  return projets.filter((p): p is Project & { id: string } => Boolean(p.id))
}

/** La destination proposée : la dernière si elle existe encore, sinon le « Non classé ». */
export function destinationParDefaut(projets: Project[], memorisee: string | null, nonClasse: string): string {
  const possibles = destinationsPossibles(projets)
  if (memorisee && possibles.some((p) => p.id === memorisee)) return memorisee
  return nonClasse
}

// ---------- Initiales (panneau replié) ----------

/**
 * Deux lettres pour reconnaître un projet quand le panneau est replié : les initiales des deux
 * premiers mots, ou les deux premières lettres d'un nom d'un seul mot (« Réunions AMI » → RA,
 * « Produit » → PR). Le nom complet reste l'infobulle et le nom accessible.
 */
export function initiales(nom: string): string {
  const mots = nom.trim().split(/[\s\-_']+/).filter(Boolean)
  const lettres = mots.length >= 2 ? mots[0][0] + mots[1][0] : (mots[0] ?? '').slice(0, 2)
  return lettres.toLocaleUpperCase()
}

// ---------- Filtre ----------

const sansAccents = (texte: string): string =>
  texte.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase()

export function filtrerProjets<T extends { name: string }>(projets: T[], texte: string): T[] {
  const cherche = sansAccents(texte.trim())
  if (!cherche) return projets
  return projets.filter((p) => sansAccents(p.name).includes(cherche))
}

// ---------- Mémoire du navigateur, par compte ----------

const cleDe = (base: string, compte: string): string => `${base}.${compte}`

export function lirePanneauReplie(compte: string): boolean {
  try {
    return lireCle(cleDe('panneau.replie', compte)) === '1'
  } catch {
    return false
  }
}

export function ecrirePanneauReplie(compte: string, replie: boolean): void {
  try {
    ecrireCle(cleDe('panneau.replie', compte), replie ? '1' : '0')
  } catch {
    /* stockage indisponible : le choix ne persiste pas, rien ne casse. */
  }
}

export function lireDestination(compte: string): string | null {
  try {
    return lireCle(cleDe('destination', compte))
  } catch {
    return null
  }
}

export function ecrireDestination(compte: string, projet: string): void {
  try {
    ecrireCle(cleDe('destination', compte), projet)
  } catch {
    /* stockage indisponible : la destination ne persiste pas. */
  }
}
