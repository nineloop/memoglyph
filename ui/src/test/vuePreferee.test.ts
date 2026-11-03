import { beforeEach, describe, expect, it } from 'vitest'
import {
  lireVueMemorisee, memoriserVue, VUES_MEMORISABLES, vueDeDepart,
} from '../lib/vuePreferee'
import { VUES } from '../lib/urlState'

/**
 * La reprise, testée SANS React — même partage que `urlState.test.ts`.
 *
 * Ce fichier vérifie la DÉCISION et le stockage. Que l'application s'ouvre
 * réellement là où ils disent est vérifié dans `App.test.tsx` : c'est un autre
 * défaut possible, et le piège explicite de ce lot — une préférence peut être
 * écrite correctement sans que sa lecture soit branchée nulle part.
 */
const CLE = 'memoglyph.vue'

beforeEach(() => { localStorage.removeItem(CLE) })

describe('mémorisation', () => {
  it('écrit ET relit une vue de travail', () => {
    memoriserVue('voices')
    expect(localStorage.getItem(CLE)).toBe('voices')
    expect(lireVueMemorisee()).toBe('voices')
  })

  it('n’écrit PAS les vues hors travail', () => {
    memoriserVue('library')
    for (const detour of ['maintenance', 'trash', 'dedup']) {
      memoriserVue(detour)
      // Rouvrir sur la corbeille parce qu'on y a restauré un document la veille
      // serait déroutant : ce sont des détours, pas des points de départ.
      expect(localStorage.getItem(CLE)).toBe('library')
    }
  })

  it('ignore une valeur de stockage inconnue', () => {
    // Version antérieure, ou stockage édité à la main : on ne bloque rien.
    for (const brut of ['chaussette', '', 'trash', 'Library', '{"vue":"ask"}']) {
      localStorage.setItem(CLE, brut)
      expect(lireVueMemorisee()).toBeNull()
    }
  })

  it('rend `null` sans rien en stockage', () => {
    expect(lireVueMemorisee()).toBeNull()
  })
})

describe('vueDeDepart', () => {
  it('donne le dernier mot à l’URL, contre la mémoire ET contre le corpus', () => {
    // Les deux sens : la préférence ne doit ni imposer sa vue, ni être
    // confondue avec l'absence de préférence.
    expect(vueDeDepart('transcribe', 'voices', false)).toBe('transcribe')
    expect(vueDeDepart('voices', 'transcribe', true)).toBe('voices')
    expect(vueDeDepart('search', null, false)).toBe('search')
  })

  it('suit la mémoire quand l’URL se tait', () => {
    expect(vueDeDepart(undefined, 'ask', false)).toBe('ask')
    expect(vueDeDepart(undefined, 'transcribe', false)).toBe('transcribe')
  })

  it('sans mémoire, ouvre Bibliothèque si le corpus a du contenu', () => {
    expect(vueDeDepart(undefined, null, false)).toBe('library')
    expect(vueDeDepart(undefined, null, true)).toBe('transcribe')
  })
})

describe('garde sur l’instrument', () => {
  it('la liste des vues mémorisables n’est ni vide ni inventée', () => {
    // Sans cette ancre, `n'écrit PAS les vues hors travail` passerait sur une
    // liste vide — la forme de contrôle creux que ce dépôt traque.
    expect(VUES_MEMORISABLES).toEqual(
      ['transcribe', 'library', 'search', 'ask', 'voices'])
    // Et chacune doit rester une vue que l'URL sait porter, sinon la reprise
    // ouvrirait un `<main>` vide.
    for (const vue of VUES_MEMORISABLES) expect(VUES).toContain(vue)
  })
})

it("« traitements » n'est pas une vue : elle n'entre jamais dans la vue d'ouverture mémorisée (chantier 003)", () => {
  memoriserVue('traitements')
  expect(lireVueMemorisee()).toBeNull()
})
