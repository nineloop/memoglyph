import { afterEach, expect, it } from 'vitest'

import { formaterInstant, formaterJour, formaterJourHeure } from '../lib/dates'

// Le projet de tests ne charge pas les types de Node : on ne déclare que ce dont ce fichier a besoin.
declare const process: { env: { TZ?: string } }

const FUSEAU_INITIAL = process.env.TZ
afterEach(() => {
  if (FUSEAU_INITIAL === undefined) delete process.env.TZ
  else process.env.TZ = FUSEAU_INITIAL
})


it('un jour se lit sur deux chiffres en français et avec le mois abrégé en anglais', () => {
  expect(formaterJour('2026-10-13', 'fr')).toBe('13/10/2026')
  expect(formaterJour('2026-10-13', 'en')).toBe('13 Oct 2026')
  expect(formaterJour('2026-10-05', 'fr')).toBe('05/10/2026')
  expect(formaterJour('2026-10-05', 'en')).toBe('5 Oct 2026')
})

it('une heure collée au jour ne change pas le jour', () => {
  expect(formaterJour('2026-10-13T23:59:59', 'fr')).toBe('13/10/2026')
  expect(formaterJour('2026-10-13T00:00:00+02:00', 'en')).toBe('13 Oct 2026')
})

it('une valeur illisible rend null : l\u2019appelant garde son texte de repli', () => {
  for (const valeur of ['', 'pas une date', '2026-13-40', '2026-02-30', '13/10/2026']) {
    expect(formaterJour(valeur, 'fr')).toBeNull()
    expect(formaterJour(valeur, 'en')).toBeNull()
  }
})

it('un jour AAAA-MM-JJ n\u2019est jamais décalé par le fuseau de la machine', () => {
  for (const fuseau of ['America/Los_Angeles', 'Pacific/Auckland', 'UTC']) {
    process.env.TZ = fuseau
    // Précondition : à l'ouest de UTC, une lecture naïve par `new Date('AAAA-MM-JJ')` donnerait la veille.
    if (fuseau === 'America/Los_Angeles') expect(new Date('2026-10-13').getDate()).toBe(12)
    expect(formaterJour('2026-10-13', 'fr')).toBe('13/10/2026')
    expect(formaterJour('2026-10-13', 'en')).toBe('13 Oct 2026')
    expect(formaterJourHeure('2026-07-23T08:30:00', 'fr')).toBe('23/07/2026 08:30')   // l'heure se lit telle quelle, sans fuseau
    expect(formaterJourHeure('2026-07-23T08:30:00', 'en')).toBe('23 Jul 2026, 08:30')
  }
})

it('un jour et une heure sans fuseau se lisent tels quels, dans chaque langue', () => {
  expect(formaterJourHeure('2026-07-23T08:30:00', 'fr')).toBe('23/07/2026 08:30')
  expect(formaterJourHeure('2026-07-23T08:30:00', 'en')).toBe('23 Jul 2026, 08:30')   // la virgule de `Intl`, comme l'horodatage des versions
  expect(formaterJourHeure('2026-07-23', 'fr')).toBeNull()
  expect(formaterJourHeure('n\u2019importe quoi', 'en')).toBeNull()
  expect(formaterJourHeure('2026-07-23T25:30:00', 'fr')).toBeNull()
  expect(formaterJourHeure('2026-07-23T08:61:00', 'fr')).toBeNull()
})

it('un instant se lit à l\u2019heure locale, avec les secondes, dans la langue choisie', () => {
  // Construit à l'heure locale : le résultat attendu ne dépend pas du fuseau de la machine.
  const secondes = new Date(2026, 9, 13, 14, 5, 9).getTime() / 1000
  const fr = formaterInstant(secondes, 'fr')
  const en = formaterInstant(secondes, 'en')
  expect(fr).toContain('13/10/2026')
  expect(fr).toContain('14:05:09')
  expect(en).toContain('13 Oct 2026')
  expect(en).toContain('14:05:09')
  expect(en).not.toContain('/')
})
