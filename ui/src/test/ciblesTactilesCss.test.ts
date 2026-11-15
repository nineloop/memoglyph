import { expect, it } from 'vitest'
import css from '../App.css?raw'

// jsdom ne dessine rien : ce test garde la RÈGLE ; les 44 px se mesurent en capture (`boundingBox()` à 390 px).
const DEBUT = '@media (max-width: 820px), (pointer: coarse) {'

it('sur écran étroit ou tactile, les petits boutons, croix, horodatages et pastilles font au moins 44 px ; la citation placée dans une phrase reste compacte (24 px)', () => {
  const i = css.indexOf(DEBUT)
  expect(i).toBeGreaterThan(-1)
  const bloc = css.slice(i, css.indexOf('\n}\n', i))
  const liste44 = bloc.match(/\n\s*([^{\n]+)\{\s*min-height:\s*44px;\s*min-width:\s*44px;/)?.[1] ?? ''
  for (const selecteur of ['.btn.sm', '.icon-btn', '.timestamp-link', '.chip']) expect(liste44).toContain(selecteur)
  expect(liste44).not.toContain('.cite')   // une citation dans une phrase relève de l'exception « en ligne » (WCAG 2.5.8), décision du 12/11
  expect(bloc).toMatch(/\.cite:not\(\.cite-missing\)\s*\{[^}]*min-height:\s*24px/)
})

it('au bureau et au pointeur fin, ces contrôles gardent leur aspect compact (aucune règle de 44 px hors du bloc tactile)', () => {
  const hors = css.replace(css.slice(css.indexOf(DEBUT), css.indexOf('\n}\n', css.indexOf(DEBUT))), '')
  expect(hors).toMatch(/\.btn\.sm \{ padding: 3px 8px;/)   // la règle compacte d'origine reste
  expect(hors).not.toMatch(/\n\.btn\.sm \{[^}]*min-height:\s*44px/)
  expect(hors).not.toMatch(/\n\.timestamp-link \{[^}]*min-height:\s*44px/)
})
