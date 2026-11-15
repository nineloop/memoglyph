import { ErreurAvecCode, ErreurHttp, EnvoiRefuseError } from '../api'
import type { Lang, Traduire } from './i18n'
import { CATALOGUES } from './messages'
import type { MessageKey } from './messages'

/**
 * La phrase d'une erreur, dans la langue choisie.
 *
 * Un code que le serveur a donné et que le catalogue connaît (`erreur.<code>`) donne SA phrase ; tout le reste (une erreur de
 * réseau, une panne, une exception, un code que cette interface ne connaît pas) donne le `repli` propre à l'écran. Jamais le
 * texte de l'exception, jamais celui du serveur, jamais « HTTP … ».
 *
 * Ce n'est pas `traduireEtape` : celle-ci rend le texte du serveur quand le code manque, ce qui convient à des étapes de
 * traitement mais pas à une erreur.
 */
export function phraseDErreur(t: Traduire, lang: Lang, erreur: unknown, repli: string): string {
  const code = codeDe(erreur)
  if (code) {
    const cle = `erreur.${code}` as MessageKey
    if (cle in CATALOGUES[lang]) return t(cle)
  }
  return repli
}

/** Le code stable que porte une erreur du client, quelle que soit la façon dont elle a été levée ; `null` sinon. */
function codeDe(erreur: unknown): string | null {
  if (erreur instanceof ErreurAvecCode || erreur instanceof ErreurHttp || erreur instanceof EnvoiRefuseError) return erreur.code
  return null
}
