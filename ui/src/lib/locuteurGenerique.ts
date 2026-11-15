/**
 * Étiquette d'un locuteur sans nom, dans la langue de l'interface.
 *
 * Le fichier transcript écrit « Speaker N » ; l'interface française dit « Locuteur N ». Seul l'AFFICHAGE change : le fichier,
 * les clés de teinte, les voix et la recherche gardent « Speaker N ». Un nom saisi par la personne passe tel quel.
 */
export function etiquetteLocuteur(locuteur: string, t: (cle: 'spoken.speakerGeneric', params: { n: string }) => string): string {
  const generique = /^Speaker (\d+)$/.exec(locuteur)
  return generique ? t('spoken.speakerGeneric', { n: generique[1] }) : locuteur
}
