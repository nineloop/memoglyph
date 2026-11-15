import { expect, it } from 'vitest'
import css from '../App.css?raw'
import audioCss from '../audio/audio.css?raw'

// jsdom ne dessine rien : ce test garde les RÈGLES ; les titres complets se voient en capture, à 390 px.
function regle(source: string, selecteur: string): string {
  const i = source.indexOf(`\n${selecteur} {`)
  return i < 0 ? '' : source.slice(i, source.indexOf('}', i) + 1)
}

it('le nom du document actif, le titre de réunion (fil d’Ariane au bureau, et liste) passent à la ligne au lieu d’être coupés ; sous 820 px le fil d’Ariane, redit par le grand titre, est coupé par une ellipse', () => {
  for (const selecteur of ['.document-label strong', '.fil-courant', '.meeting-title']) {
    const r = regle(css, selecteur)
    expect(r, selecteur).not.toBe('')
    expect(r, selecteur).not.toMatch(/white-space:\s*nowrap/)
    expect(r, selecteur).not.toMatch(/text-overflow:\s*ellipsis/)
    expect(r, selecteur).toMatch(/overflow-wrap:\s*anywhere/)
  }
  // Au bureau, le titre courant du fil d'Ariane ne gagne pas une ligne alors que la place existe (il était plafonné à 38ch).
  expect(regle(css, '.fil-courant')).toMatch(/max-width:\s*70ch/)
  // Sous 820 px, le grand titre de la réunion le redit juste dessous : le fil d'Ariane le coupe par une ellipse, sur une ligne.
  const i = css.indexOf('@media (max-width: 820px) {\n  /* Le grand titre')
  expect(i).toBeGreaterThan(-1)
  // Et le bloc peut rétrécir dans sa carte (`min-width: 0`) : sans cela la ligne sans coupure déborde au lieu de s'abréger.
  expect(css.slice(i, css.indexOf('\n}\n', i))).toMatch(/\.fil-ariane ol\s*\{[^}]*min-width:\s*0[\s\S]*\.fil-courant\s*\{[^}]*white-space:\s*nowrap[^}]*text-overflow:\s*ellipsis/)
})

it('le titre du mini-lecteur passe à la ligne sur petit écran ou au toucher (au pointeur fin, l’ellipse et l’info-bulle restent)', () => {
  expect(regle(audioCss, '.mini-title')).toMatch(/text-overflow:\s*ellipsis/)
  const i = audioCss.indexOf('@media (max-width: 820px), (pointer: coarse) {')
  expect(i).toBeGreaterThan(-1)
  const bloc = audioCss.slice(i, audioCss.indexOf('\n}\n', i))
  expect(bloc).toMatch(/\.mini-title\s*\{[^}]*white-space:\s*normal/)
  expect(bloc).toMatch(/\.mini-title\s*\{[^}]*overflow-wrap:\s*anywhere/)
})
