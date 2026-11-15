import { expect, it } from 'vitest'
import css from '../App.css?raw'

// jsdom ne calcule pas la mise en page : ce test garde les RÈGLES ; la preuve vient des captures à 390 px.
it('sur mobile, la rangée d’onglets de la Maintenance montre par un dégradé de bord qu’elle défile, et la ligne d’un doublon se replie', () => {
  const media = (largeur: number) => {
    const i = css.indexOf(`@media (max-width: ${largeur}px) {\n  .maintenance-tabs`)
    return i < 0 ? '' : css.slice(i, css.indexOf('\n}\n', i))
  }
  expect(media(820)).toMatch(/\.maintenance-tabs\s*\{[^}]*mask-image:\s*linear-gradient/)
  expect(css).toMatch(/@media \(max-width: 600px\) \{\s*\.dup-group \.lib-item \{ flex-wrap: wrap; \}/)
})
