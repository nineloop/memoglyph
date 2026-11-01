import { afterEach, describe, expect, it } from 'vitest'
import { cle, ecrireCle, lireCle, retirerCle } from '../lib/stockage'

/**
 * Les préférences du navigateur vivent sous `memoglyph.*`.
 */
describe('stockage — clés sous le préfixe du produit', () => {
  afterEach(() => {
    localStorage.clear()
  })

  it('écrit sous le nom actuel', () => {
    ecrireCle('theme', 'dark')
    expect(localStorage.getItem('memoglyph.theme')).toBe('dark')
    expect(cle('theme')).toBe('memoglyph.theme')
  })

  it('lit la valeur rangée sous le nom actuel, et rien sinon', () => {
    localStorage.setItem('memoglyph.vue', 'search')
    localStorage.setItem('vue', 'library')
    expect(lireCle('vue')).toBe('search')
    expect(lireCle('absent')).toBeNull()
  })

  it('une valeur retirée ne se relit plus', () => {
    ecrireCle('recent', '{"name":"a.md"}')
    retirerCle('recent')
    expect(localStorage.getItem('memoglyph.recent')).toBeNull()
    expect(lireCle('recent')).toBeNull()
  })
})
