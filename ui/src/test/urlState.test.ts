import { describe, expect, it } from 'vitest'
import { ecrireUrl, lireUrl, ONGLETS_REUNION, VUES } from '../lib/urlState'
import type { EtatUrl } from '../lib/urlState'

/**
 * Le module d'état URL, testé SANS React.
 *
 * C'est délibéré : ces fonctions sont pures, et les vérifier isolément est le
 * seul moment où l'on peut distinguer un défaut de parsing d'un défaut de
 * câblage. Le branchement dans `App.tsx` a ses propres tests.
 */
describe('lireUrl', () => {
  it('NE REND AUCUNE vue sur une URL vide', () => {
    // Le cœur de la vue préférée : « pas de `vue` » n'est pas « `vue=transcribe` ».
    // Tant que les deux rendaient la même chose, personne ne pouvait choisir
    // une vue d'ouverture sans écraser les liens partagés.
    expect(lireUrl('')).toEqual({})
    expect(lireUrl('?projet=01P&reunion=01M').vue).toBeUndefined()
  })

  it('rend `transcribe` quand la vue est ÉCRITE', () => {
    // L'autre moitié de la distinction. Sans cette assertion, un `lireUrl` qui
    // laisserait tomber toute vue passerait le test précédent.
    expect(lireUrl('?vue=transcribe').vue).toBe('transcribe')
    expect('vue' in lireUrl('?vue=transcribe')).toBe(true)
  })

  it('lit les cinq clés', () => {
    expect(lireUrl('?vue=library&projet=01ABC&reunion=01XYZ&onglet=cr&q=climat'))
      .toEqual({
        vue: 'library', projet: '01ABC', reunion: '01XYZ',
        onglet: 'cr', q: 'climat',
      })
  })

  it('RETOMBE sur transcribe pour une vue inconnue', () => {
    // Pas cosmétique : les blocs de rendu d'App sont gardés par `tab === '…'`.
    // Une valeur hors liste afficherait un `<main>` VIDE pendant que l'en-tête
    // retombe déjà sur le premier onglet — écran incohérent, pas seulement faux.
    for (const vue of ['inconnue', '', 'Library', '../etc/passwd', 'dedup ']) {
      expect(lireUrl(`?vue=${encodeURIComponent(vue)}`).vue).toBe('transcribe')
    }
  })

  it("porte l'onglet Faits : un lien ou un rechargement le rouvre", () => {
    expect(lireUrl('?vue=library&projet=all&reunion=01X&onglet=faits').onglet).toBe('faits')
    expect(ecrireUrl({ vue: 'library', projet: 'all', reunion: '01X', onglet: 'faits' }))
      .toBe('?vue=library&projet=all&reunion=01X&onglet=faits')
  })

  it('ignore un onglet de réunion inconnu', () => {
    expect(lireUrl('?vue=library&reunion=01X&onglet=nimporte').onglet).toBeUndefined()
  })

  it('REFUSE la sentinelle de création de projet', () => {
    // `__new__` déclenche une boîte de dialogue et ne désigne aucune portée.
    // Dans l'URL, un rechargement la rouvrirait à chaque fois.
    expect(lireUrl('?projet=__new__').projet).toBeUndefined()
  })

  it('accepte toutes les vues déclarées', () => {
    for (const vue of VUES) expect(lireUrl(`?vue=${vue}`).vue).toBe(vue)
  })

  it('accepte tous les onglets déclarés', () => {
    for (const onglet of ONGLETS_REUNION) {
      expect(lireUrl(`?reunion=01X&onglet=${onglet}`).onglet).toBe(onglet)
    }
  })
})

describe('ecrireUrl', () => {
  it('omet les clés vides', () => {
    expect(ecrireUrl({ vue: 'search' })).toBe('?vue=search')
  })

  it('sérialise dans un ORDRE FIXE', () => {
    // Sans ordre stable, deux états égaux produiraient des chaînes différentes
    // et la comparaison anti-doublon d'historique battrait la chamade.
    const etat: EtatUrl = {
      q: 'climat', onglet: 'cr', reunion: '01X', projet: '01P', vue: 'library',
    }
    expect(ecrireUrl(etat))
      .toBe('?vue=library&projet=01P&reunion=01X&onglet=cr&q=climat')
  })

  it('omet un onglet SANS réunion', () => {
    // Un onglet de réunion n'a aucun sens seul — le laisser produirait une URL
    // qu'on ne saurait pas relire.
    expect(ecrireUrl({ vue: 'library', onglet: 'cr' })).toBe('?vue=library')
  })

  it('n’écrit JAMAIS la sentinelle de création', () => {
    expect(ecrireUrl({ vue: 'library', projet: '__new__' })).toBe('?vue=library')
  })
})

describe('aller-retour', () => {
  it('relit ce qu’il a écrit', () => {
    const etats: EtatUrl[] = [
      { vue: 'transcribe' },
      { vue: 'search', q: 'budget trimestriel' },
      { vue: 'library', projet: '01P', reunion: '01M', onglet: 'versions' },
      { vue: 'voices', projet: 'all' },
    ]
    for (const etat of etats) expect(lireUrl(ecrireUrl(etat))).toEqual(etat)
  })

  it('survit aux caractères qui doivent être échappés', () => {
    const etat: EtatUrl = { vue: 'search', q: 'coût & délai « 2026 »' }
    expect(lireUrl(ecrireUrl(etat))).toEqual(etat)
  })
})

// `memeUrl` a été RETIRÉ avant d'être livré.
//
// Je l'avais écrit pour la comparaison anti-doublon d'historique, puis
// `App.tsx` a comparé directement les chaînes — plus simple, et le seul
// appelant possible. Un export sans appelant, avec ses deux tests, aurait été
// du code vérifié pour rien : la forme exacte de dette que cette journée a
// passé à retirer ailleurs.

describe('garde sur l’instrument', () => {
  it('les listes de valeurs ne sont pas vides', () => {
    // Sans cette ancre, les tests « accepte toutes les vues déclarées » et
    // « accepte tous les onglets » passeraient sur une liste vide sans rien
    // vérifier — la forme de contrôle creux traquée toute la journée.
    expect(VUES.length).toBeGreaterThanOrEqual(6)
    expect(ONGLETS_REUNION.length).toBe(5)  // Faits compris
  })
})


describe("le panneau des traitements dans l'URL", () => {
  it("lit et écrit `traitements=1`, et rien d'autre ne change", () => {
    expect(lireUrl('?traitements=1').traitements).toBe(true)
    expect(lireUrl('?vue=library&projet=01P&traitements=1')).toEqual({ vue: 'library', projet: '01P', traitements: true })
    expect(ecrireUrl({ vue: 'library', projet: '01P', traitements: true })).toBe('?vue=library&projet=01P&traitements=1')
  })

  it("ignore toute autre valeur que 1 et ne l'écrit pas quand il est faux", () => {
    expect(lireUrl('?traitements=0').traitements).toBeUndefined()
    expect(lireUrl('?traitements=oui').traitements).toBeUndefined()
    expect(ecrireUrl({ vue: 'library', traitements: false })).toBe('?vue=library')
  })

  it("les anciens liens donnent les mêmes états qu'avant", () => {
    expect(lireUrl('?vue=library&projet=01P&reunion=01M&onglet=transcript'))
      .toEqual({ vue: 'library', projet: '01P', reunion: '01M', onglet: 'transcript' })
    expect(lireUrl('?vue=search&q=climat')).toEqual({ vue: 'search', q: 'climat' })
    expect(lireUrl('')).toEqual({})
  })
})
