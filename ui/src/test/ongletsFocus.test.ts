import { expect, it } from 'vitest'
import css from '../App.css?raw'

// jsdom ne dessine rien : ce test garde la RÈGLE ; l'anneau lui-même se voit en capture (focus au clavier sur l'onglet sélectionné).
function regle(selecteur: string): string {
  const i = css.indexOf(`\n${selecteur}`)
  return i < 0 ? '' : css.slice(i, css.indexOf('}', i) + 1)
}

it('l’onglet focalisé au clavier garde son anneau de focus, malgré l’ombre de l’onglet sélectionné', () => {
  const ombres = regle('.meeting-tabs button[aria-selected]:focus-visible')
  expect(ombres).toMatch(/\.maintenance-tabs button\.on:focus-visible/)
  expect(ombres).toMatch(/box-shadow:\s*var\(--focus\)/)
})


it('l’onglet focalisé au clavier a aussi un contour plein en couleur d’accent, lisible dans les deux thèmes', () => {
  const r = regle('.meeting-tabs button[aria-selected]:focus-visible')
  expect(r).toMatch(/outline:\s*2px solid var\(--accent\)/)
  expect(r).toMatch(/outline-offset:\s*2px/)
})
