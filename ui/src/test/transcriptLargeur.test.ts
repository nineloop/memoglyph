import { describe, expect, it } from 'vitest'
import css from '../App.css?raw'

// jsdom ne calcule pas la mise en page : ces tests gardent les RÈGLES du transcript pleine largeur (SC-007,
// FR-018) ; les largeurs réelles sont mesurées sur la démo par `tools/mesure_ui.mjs` et vues sur captures.

/** Le bloc d'une règle @media (de son ouverture à son accolade fermante). */
function bloc(media: string): string {
  const debut = css.indexOf(media)
  if (debut < 0) return ''
  let profondeur = 0
  for (let i = css.indexOf('{', debut); i < css.length; i++) {
    if (css[i] === '{') profondeur++
    if (css[i] === '}' && --profondeur === 0) return css.slice(debut, i + 1)
  }
  return ''
}

/** Le CSS HORS de la requête média des grands écrans (au-delà de 1600 px). */
const GRAND = '@media (min-width: 1601px)'
const horsGrandEcran = css.replace(bloc(GRAND), '')

describe('le transcript prend toute la largeur utile (priorité à la largeur)', () => {
  it('la Bibliothèque n\'est plus bridée à 1240 px, les autres écrans le restent', () => {
    expect(css).toMatch(/\.app-main\s*\{[^}]*max-width:\s*1240px/)                 // Transcrire, Recherche… inchangés
    expect(css).toMatch(/\.app-main\.app-main--bibliotheque\s*\{[^}]*max-width:\s*none/)
  })

  it('aucune limite de longueur de ligne jusqu\'à 1600 px', () => {
    for (const selecteur of ['\\.transcript-texte', '\\.tour', '\\.tour-passages', '\\.tour-passages \\.tline', '\\.transcript-lecture', '\\.transcript-reader']) {
      const regle = new RegExp(`${selecteur}\\s*\\{[^}]*max-width`)
      expect(horsGrandEcran, selecteur).not.toMatch(regle)
    }
  })

  it('au-delà de 1600 px, la ligne est bornée à environ 120 caractères (90ch : le « 0 » est plus large qu\'un caractère courant)', () => {
    const grand = bloc(GRAND)
    expect(grand).toMatch(/\.tour-passages \.tline\s*\{[^}]*max-width:\s*90ch/)
  })

  it('chaque tour de parole est sur UNE colonne : le texte n\'est plus coupé de 144 px par la colonne du locuteur', () => {
    expect(css).toMatch(/\.tour\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/)
    expect(css).not.toMatch(/\.tour\s*\{[^}]*grid-template-columns:\s*128px/)
  })

  it('le panneau des locuteurs reste à côté du texte (196 px) et passe au-dessus du texte sous 600 px', () => {
    expect(css).toMatch(/\.transcript-lecture\s*\{[^}]*grid-template-columns:\s*196px minmax\(0,\s*1fr\)/)
    // Il y a plusieurs requêtes « max-width: 600px » : on cherche celle qui empile le panneau au-dessus du texte.
    expect(css).toMatch(/@media \(max-width: 600px\)\s*\{(?:(?!@media)[\s\S])*?\.transcript-lecture\s*\{\s*grid-template-columns:\s*minmax\(0,\s*1fr\)/)
  })

  it('le texte se replie au lieu de faire défiler horizontalement (390 px)', () => {
    expect(css).toMatch(/\.tour-passages \.tline\s*\{[^}]*overflow-wrap:\s*anywhere/)
  })
})

describe('cibles de la vue d\'une réunion', () => {
  it('les onglets, les réserves de qualité et le bouton « Retour » font au moins 44 px à toutes les largeurs', () => {
    expect(css).toMatch(/\.meeting-tabs button\s*\{[^}]*min-height:\s*44px/)
    expect(css).toMatch(/\.meeting-reserves > button\s*\{[^}]*min-height:\s*44px/)
    expect(css).toMatch(/\.meeting-hero-top \.btn\s*\{[^}]*min-height:\s*44px/)
  })

  it('le panneau des locuteurs (locuteurs et repères) et la barre d\'actions du document font au moins 44 px', () => {
    expect(css).toMatch(/\.transcript-rail button\s*\{[^}]*min-height:\s*44px/)
    expect(css).toMatch(/\.document-toolbar \.btn\s*\{[^}]*min-height:\s*44px/)
  })

  it('les horodatages ont une zone d\'au moins 24 px au pointeur fin (WCAG 2.5.8)', () => {
    expect(css).toMatch(/\.tour-passages \.ts\s*\{[^}]*min-height:\s*24px/)
  })

  it('et 44 px au toucher ou sur petit écran', () => {
    const tactile = bloc('@media (pointer: coarse), (max-width: 820px)')
    expect(tactile).toMatch(/\.tour-passages \.ts\s*\{[^}]*min-height:\s*44px/)
  })
})

describe('les éléments collants de la vue d\'une réunion s\'empilent sans se recouvrir', () => {
  // Les onglets (44 px de bouton, 5 px de marge de chaque côté, 1 px de bordure) mesurent 56 px : le lecteur audio
  // se colle SOUS eux, le panneau des locuteurs sous le lecteur. Onglets de 34 px avant le chantier : 46 px.
  const px = (re: RegExp, source = css): number => Number(re.exec(source)?.[1])
  const hauteurOnglets = px(/\.meeting-tabs button\s*\{[^}]*min-height:\s*(\d+)px/) + 2 * 5 + 2
  const hauteurLecteur = 56   // marge 10 + ligne de contrôle ≈ 36 + bordures ; mesuré sur la démo

  it('le lecteur est collé sous les onglets (bureau)', () => {
    const hautOnglets = px(/\.meeting-tabs\s*\{[^}]*position:\s*sticky;\s*top:\s*(\d+)px/)
    const hautLecteur = px(/\.meeting-workspace \.transcript-reader \.player\s*\{[^}]*top:\s*(\d+)px/)
    expect(hautLecteur).toBeGreaterThanOrEqual(hautOnglets + hauteurOnglets)
  })

  it('le panneau des locuteurs est collé sous le lecteur (bureau)', () => {
    const hautLecteur = px(/\.meeting-workspace \.transcript-reader \.player\s*\{[^}]*top:\s*(\d+)px/)
    const hautRail = px(/\.meeting-workspace \.transcript-rail\s*\{[^}]*top:\s*(\d+)px/)
    expect(hautRail).toBeGreaterThanOrEqual(hautLecteur + hauteurLecteur)
  })

  it('sur petit écran, les onglets (sur deux lignes) ne collent plus : le lecteur se pose sous la seule barre haute', () => {
    const petit = css.slice(css.indexOf('@media (max-width: 600px)'))
    expect(petit).toMatch(/\.meeting-tabs\s*\{[^}]*position:\s*static/)            // plus collants : rien à recouvrir
    const hautLecteurMobile = px(/@media[^{]*\{(?:(?!@media)[\s\S])*?\.meeting-workspace \.transcript-reader \.player\s*\{\s*top:\s*(\d+)px/)
    expect(hautLecteurMobile).toBeGreaterThanOrEqual(123)                              // la barre haute mesurée à 390 px
  })
})
