import type { Traduire } from './i18n'

/** Le nom que le serveur donne à la réception de chaque compte (constante stockée, jamais traduite). */
export const NOM_RECEPTION = 'Non classé'

/**
 * Le nom d'un projet tel qu'on l'AFFICHE.
 *
 * « Non classé » est un libellé du système, pas un nom choisi : en anglais on lit « Unclassified »
 * (le mélange des langues est interdit). Le nom stocké reste inchangé ; seule l'affichage passe
 * par ici. Un projet nommé par l'utilisateur n'est jamais touché.
 */
export function libelleProjet(nom: string, t: Traduire, kind?: string): string {
  return kind === 'inbox' || nom === NOM_RECEPTION ? t('common.unclassified') : nom
}
