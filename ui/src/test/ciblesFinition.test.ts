import { describe, expect, it } from 'vitest'
import css from '../App.css?raw'
import audio from '../audio/audio.css?raw'

// jsdom ne calcule pas la mise en page : ces tests gardent les RÈGLES de cibles de 44 px posées à la finition
// ; les tailles réelles sont mesurées sur la démo par `tools/mesure_ui.mjs`.

const regle = (source: string, selecteur: string): string => {
  const echappe = selecteur.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const trouve = [...source.matchAll(new RegExp(`(^|[},\\n])\\s*[^{}]*${echappe}[^{}]*\\{([^}]*)\\}`, 'g'))]
  return trouve.map((m) => m[2]).join(' ')
}

describe('cibles de 44 px', () => {
  const tailles44 = (corps: string) => /min-height:\s*44px/.test(corps) || /height:\s*44px/.test(corps)

  it.each([
    '.report-switcher button',
    '.report-toolbar-actions .btn',
    '.version-actions .btn',
    '.ask-amorces .btn',
    '.workspace-heading .btn',
    '.trash-heading-actions .btn',
    '.badges .btn',
    '.index-actions .btn',
    '.maintenance-tabs button',
    '.option-select',
    '.reglages-menu .lang-select',
    '.reglages-menu .kw-seg__item',
  ])('%s a une cible d\'au moins 44 px', (selecteur) => {
    expect(tailles44(regle(css.slice(css.indexOf('Finition des cibles tactiles')), selecteur)), selecteur).toBe(true)
  })

  it('le mini-lecteur : lecture, curseur et fermeture font 44 px', () => {
    expect(regle(audio, '.mini-play')).toMatch(/width:\s*44px;\s*height:\s*44px/)
    expect(regle(audio, '.mini-close')).toMatch(/width:\s*44px;\s*height:\s*44px/)
    expect(regle(audio, '.mini-range')).toMatch(/height:\s*44px/)
  })

  it('les repères de source d\'un compte rendu suivent la règle des horodatages : 24 px, 44 px au toucher', () => {
    const fin = css.slice(css.indexOf('Finition des cibles tactiles'))
    expect(fin).toMatch(/\.source-chip\s*\{[^}]*min-height:\s*24px/)
    const toucher = fin.slice(fin.indexOf('@media (pointer: coarse), (max-width: 820px)'))
    expect(toucher).toMatch(/\.source-chip\s*\{[^}]*min-height:\s*44px;\s*min-width:\s*44px/)
  })

  it('petit écran : le rail est replié, les onglets de la réunion passent à la ligne', () => {
    const petit = css.slice(css.indexOf('@media (max-width: 600px)'))
    expect(petit).toMatch(/\.rail-contenu:not\(\.ouvert\)\s*\{\s*display:\s*none/)
    expect(petit).toMatch(/\.rail-bascule\s*\{[^}]*display:\s*flex;[^}]*min-height:\s*44px/)
    expect(petit).toMatch(/\.meeting-tabs\s*\{[^}]*position:\s*static;[^}]*flex-wrap:\s*wrap/)       // à la ligne, et plus collants
    expect(petit).toMatch(/\.meeting-workspace \.transcript-reader \.player\s*\{\s*top:\s*128px/)   // le lecteur se pose sous la seule barre haute
    // Au bureau, le bouton n'existe pas : le rail reste affiché.
    expect(css).toMatch(/@media \(min-width: 601px\)\s*\{\s*\.rail-bascule\s*\{\s*display:\s*none/)
  })
})
