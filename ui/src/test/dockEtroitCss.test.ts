import { expect, it } from 'vitest'
import css from '../App.css?raw'

// jsdom ne dessine rien : ce test garde la RÈGLE ; la largeur du texte contre celle de l'entrée se mesure en capture (320, 360, 390 px).
it('une entrée du dock ne peut pas être plus étroite que son libellé, et le dock se resserre sous 360 px', () => {
  const i = css.indexOf('  .dock-item, .dock-plus { flex: 1 1 0;')
  expect(i).toBeGreaterThan(-1)
  expect(css.slice(i, css.indexOf('\n', i))).toMatch(/min-width:\s*min-content/)
  const j = css.indexOf('@media (max-width: 360px)')
  expect(j).toBeGreaterThan(-1)
  expect(css.slice(j, css.indexOf('\n}\n', j))).toMatch(/\.dock\s*\{[^}]*padding:\s*5px/)
})
