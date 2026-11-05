import { describe, expect, it } from 'vitest'
import css from '../App.css?raw'
import indexCss from '../index.css?raw'

// jsdom ne calcule pas la mise en page : ces tests gardent les RÈGLES (fond du dock, cibles de
// 44 px, mouvement réduit) ; la preuve visuelle vient des captures, le contraste de contraste.test.ts.
function regle(selecteur: string): string {
  const i = css.indexOf(`\n${selecteur} {`)
  if (i < 0) return ''
  return css.slice(i, css.indexOf('}', i) + 1)
}

describe('le dock', () => {
  it('prend son fond et son texte dans les jetons dédiés, plus foncés que la page', () => {
    expect(regle('.dock')).toMatch(/background:\s*var\(--dock-bg\)/)
    expect(regle('.dock-item')).toMatch(/color:\s*var\(--dock-muted\)/)
    expect(indexCss).toMatch(/--dock-bg:/)
    expect(indexCss).toMatch(/--dock-fg:/)
  })

  it('se détache aussi par une bordure et une ombre, pas seulement par la couleur', () => {
    expect(regle('.dock')).toMatch(/box-shadow:[^;]*inset 0 0 0 1px/)
  })
})

describe('cibles tactiles d\'au moins 44 px (accessibilité)', () => {
  it.each(['.dock-item', '.tiroir-bouton', '.reglages-bouton', '.projets-liste a', '.navigation-reunion .icon-btn'])(
    '%s', (selecteur) => {
      expect(css).toMatch(new RegExp(`${selecteur.replace(/[.]/g, '\\.')}[^{]*\\{[^}]*min-(height|width):\\s*44px`))
    })
})

describe('mouvement réduit', () => {
  it('coupe les transitions du panneau et du tiroir quand l\'utilisateur le demande', () => {
    const i = css.indexOf('@media (prefers-reduced-motion: reduce)')
    expect(i).toBeGreaterThan(-1)
    expect(css.slice(i)).toMatch(/\.tiroir[^{]*\{[^}]*transition:\s*none/)
  })
})
