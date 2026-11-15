import { describe, expect, it } from 'vitest'
import css from '../App.css?raw'

// jsdom ne calcule pas la mise en page : on garde la RÈGLE (la preuve visuelle vient des captures).

describe('CSS du sélecteur de fournisseur', () => {
  it('le texte des lignes est aligné à gauche (un bouton centre son texte par défaut) et suit la police de la page', () => {
    expect(css).toMatch(/\.selecteur-fournisseur\s*\{[^}]*text-align:\s*left/)
    expect(css).toMatch(/\.selecteur-bascule\s*\{[^}]*font:\s*inherit/)
  })

  it('l\'intitulé « Local » a le poids du bouton « En ligne » sans en être un', () => {
    expect(css).toMatch(/\.selecteur-intitule\s*\{[^}]*font-weight:\s*600/)
    expect(css).toMatch(/\.selecteur-bascule\s*\{[^}]*font-weight:\s*600/)
  })

  it('la liste « En ligne » repliée ne garde ni espace ni hauteur (l\'attribut `hidden` l\'emporte sur la grille)', () => {
    expect(css).toMatch(/\.selecteur-fournisseur ul\[hidden\]\s*\{\s*display:\s*none/)
  })

  it('le nom et l\'état d\'un fournisseur non configuré s\'empilent avec son aide, sans se coller', () => {
    expect(css).toMatch(/\.selecteur-indispo\s*\{[^}]*flex-direction:\s*column/)
    expect(css).toMatch(/\.selecteur-etat\s*\{[^}]*margin-left/)
  })

  it('ancre : la règle générale des autres tuiles est toujours celle du second span', () => {
    expect(css).toMatch(/\.option-tile > span:nth-of-type\(2\)\s*\{[^}]*flex-direction:\s*column/)
  })
})

describe('CSS de l\'écran Transcrire en deux zones', () => {
  it('la colonne des libellés des paramètres experts est assez large pour « Rédaction » (42 px le faisait déborder sur la valeur)', () => {
    const regle = css.match(/\.model-facts > div\s*\{[^}]*grid-template-columns:\s*(\d+)px/)
    expect(regle).not.toBeNull()
    expect(Number(regle![1])).toBeGreaterThanOrEqual(72)
  })

  it('deux colonnes en bureau : les réglages à gauche, le travail à droite', () => {
    expect(css).toMatch(/\.transcribe-layout\s*\{[^}]*grid-template-columns:\s*minmax\(300px,[^)]*\)\s*minmax\(0,/)
  })

  it('« Ranger dans » et le bouton d\'envoi partagent une ligne, le bouton ne prend que sa largeur', () => {
    expect(css).toMatch(/\.ligne-envoi\s*\{[^}]*display:\s*flex/)
    // Base 0 (et non `auto`) : la barre porte aussi le nom du fichier sur sa propre ligne ; avec `auto`, le champ pousserait le bouton à la ligne.
    expect(css).toMatch(/\.ligne-envoi > \.ranger-dans\s*\{[^}]*flex:\s*1 1 0[;\s]/)
    expect(css).toMatch(/\.ligne-envoi \.transcribe-submit\s*\{[^}]*flex:\s*0 0 auto[^}]*width:\s*auto/)
  })

  it('le bouton d\'ouverture des réglages est caché en bureau et paraît sur petit écran', () => {
    // Les deux sélecteurs sont plus précis que `.icon-btn` et `.btn` : sinon la croix et le bouton s'affichent aussi en bureau.
    expect(css).toMatch(/\.studio-panel-heading \.reglages-ouvrir,\s*\.studio-panel-heading \.reglages-fermer\s*\{\s*display:\s*none/)
    const petit = css.slice(css.indexOf('@media (max-width: 1000px) {\n  .transcribe-hero'))
    expect(petit).toMatch(/\.studio-panel-heading \.reglages-ouvrir\s*\{[^}]*display:\s*inline-flex[^}]*min-height:\s*44px/)
    expect(petit).toMatch(/\.studio-panel-heading \.reglages-fermer\s*\{[^}]*display:\s*grid[^}]*min-width:\s*44px[^}]*min-height:\s*44px/)
  })

  it('sur petit écran, le panneau fermé est absent et le panneau ouvert couvre l\'écran', () => {
    const petit = css.slice(css.indexOf('@media (max-width: 1000px) {\n  .transcribe-hero'))
    expect(petit).toMatch(/\.reglages-panel:not\(\.ouvert\)\s*\{\s*display:\s*none/)
    expect(petit).toMatch(/\.reglages-panel\.ouvert\s*\{[^}]*position:\s*fixed[^}]*inset:\s*0[^}]*overflow-y:\s*auto/)
  })

  it('un fichier choisi, sur petit écran : la zone de dépôt se resserre et les sous-titres s\'effacent (le bouton reste visible)', () => {
    const petit = css.slice(css.indexOf('@media (max-width: 600px) {\n  .transcribe-hero h1'))
    expect(petit).toMatch(/\.studio-dropzone\.has-file\s*\{[^}]*min-height:\s*0/)
    expect(petit).toMatch(/\.transcribe-hero p\s*\{\s*display:\s*none/)
    expect(petit).toMatch(/\.file-step \.studio-panel-heading p\s*\{\s*display:\s*none/)
  })
})
