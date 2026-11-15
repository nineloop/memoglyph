import { expect, it } from 'vitest'
import css from '../App.css?raw'

// jsdom ne calcule pas la mise en page : ce test garde la RÈGLE ; la preuve que « Lancer la transcription » est visible vient des captures
// (position du bouton mesurée contre la hauteur de la fenêtre moins celle du dock).
function regle(selecteur: string): string {
  const i = css.indexOf(`\n${selecteur} {`)
  return i < 0 ? '' : css.slice(i, css.indexOf('}', i) + 1)
}

it('la barre d’envoi de Transcrire est collante, au-dessus du dock et sous son z-index', () => {
  const barre = regle('.ligne-envoi')
  expect(barre).toMatch(/position:\s*sticky/)
  expect(barre).toMatch(/bottom:\s*\d+px/)
  expect(Number(/z-index:\s*(\d+)/.exec(barre)?.[1])).toBeLessThan(55)   // le dock est à 55
  expect(barre).toMatch(/background:\s*var\(--panel\)/)                  // opaque : le texte passe dessous sans se mêler
})
