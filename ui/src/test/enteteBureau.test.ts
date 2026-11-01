import { describe, expect, it } from 'vitest'
import css from '../App.css?raw'

// jsdom ne calcule pas la mise en page : ce test garde la RÈGLE qui tient la barre de
// contexte sur une ligne au bureau étroit, la preuve visuelle est faite par les captures.
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

describe('barre de contexte au bureau étroit', () => {
  const regle = bloc('@media (min-width: 821px) and (max-width: 1599px)')

  it("resserre la barre sur une ligne entre 821 et 1599 px, au lieu d'en couper la fin", () => {
    expect(regle).toMatch(/\.contextbar\s*\{[^}]*gap:\s*8px/)
    expect(regle).toMatch(/\.global-search\s*\{[^}]*min-width:\s*110px/)
    expect(regle).toMatch(/\.contextbar \.scope-picker\s*\{[^}]*min-width:\s*140px/)
    expect(regle).toMatch(/\.contextbar \.chip\s*\{[^}]*padding-inline:\s*10px/)
  })

  it('ne fait pas passer la barre sur deux lignes : les éléments collants se placent à 76 px', () => {
    expect(regle).not.toMatch(/flex-wrap:\s*wrap/)
    expect(css).toMatch(/position:\s*sticky;\s*top:\s*76px/)
  })
})
