import { describe, expect, it } from 'vitest'
import css from '../App.css?raw'

// jsdom ne calcule pas la mise en page : on garde les RÈGLES (pastille mobile, cibles de 44 px,
// mouvement réduit) ; la preuve visuelle vient des captures.
describe('CSS des traitements', () => {
  it('l\'indicateur et ses cibles font au moins 44 px', () => {
    expect(css).toMatch(/\.indicateur-traitements\s*\{[^}]*min-height:\s*44px/)
    expect(css).toMatch(/\.traitement-ouvrir\s*\{[^}]*min-height:\s*44px/)
    expect(css).toMatch(/\.panneau-traitements-tete \.icon-btn\s*\{[^}]*min-width:\s*44px/)
  })

  it('sur mobile, le texte de l\'indicateur cède la place à une pastille avec le nombre', () => {
    expect(css).toMatch(/\.indicateur-pastille\s*\{\s*display:\s*none/)
    const i = css.indexOf('/* Mobile : l\'indicateur se réduit')
    expect(i).toBeGreaterThan(-1)
    const mobile = css.slice(i, i + 700)
    expect(mobile).toMatch(/\.indicateur-texte\s*\{\s*display:\s*none/)
    expect(mobile).toMatch(/\.indicateur-pastille\s*\{\s*display:\s*grid/)
  })

  it('mobile : le panneau remplit l\'espace entre la barre et le dock, sans laisser voir la page', () => {
    const i = css.indexOf('/* Mobile : le panneau des traitements')
    expect(i).toBeGreaterThan(-1)
    const mobile = css.slice(i, i + 600)
    // Collé en haut sous la barre ET en bas au-dessus du dock : il défile à l'intérieur.
    expect(mobile).toMatch(/\.panneau-traitements\s*\{[^}]*top:\s*\d+px/)
    expect(mobile).toMatch(/\.panneau-traitements\s*\{[^}]*bottom:\s*\d+px/)
    expect(mobile).toMatch(/\.panneau-traitements\s*\{[^}]*max-height:\s*none/)
    // Sous le dock (z-index 55) : le dock arrondi recouvre son bas, plus de filet de page entre les deux.
    expect(mobile).toMatch(/\.panneau-traitements\s*\{[^}]*z-index:\s*54/)
  })

  it('coupe l\'animation du signal quand le mouvement réduit est demandé', () => {
    const i = css.lastIndexOf('@media (prefers-reduced-motion: reduce)')
    expect(css.slice(i)).toMatch(/\.indicateur-signal \.icon[^{]*\{[^}]*animation:\s*none/)
  })
})
