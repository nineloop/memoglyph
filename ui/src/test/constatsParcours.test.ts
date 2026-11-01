import { describe, expect, it } from 'vitest'

import { translate } from '../lib/i18n'
import { reservesDe } from '../lib/reserves'

// Constats de la recette du parcours complet.

describe('les réserves suivent la langue de l’interface (constat 3)', () => {
  const meta = { repeated_series: ['00:01:45 « Okay. » ×3', '00:04:55 « Okay. » ×3'] }

  it('en anglais', () => {
    const r = reservesDe('Transcript', meta, (k, p) => translate('en', k, p))
    expect(r[0].texte).toBe('2 repeated series flagged')
  })

  it('en français, et par défaut', () => {
    expect(reservesDe('Transcript', meta, (k, p) => translate('fr', k, p))[0].texte).toBe('2 séries répétées signalées')
    expect(reservesDe('Transcript', meta)[0].texte).toBe('2 séries répétées signalées')
  })
})

describe('une diarisation manquée dit sa cause (constat 2)', () => {
  const cause = 'DiarizationError: HF_TOKEN manquant.'

  it.each([
    ['en', 'Speakers not identified: diarization failed'],
    ['fr', 'Locuteurs non identifiés : la diarisation a échoué'],
  ] as const)('%s', (lang, texte) => {
    const r = reservesDe('Transcript', { diarization_error: cause }, (k, p) => translate(lang, k, p))
    expect(r).toEqual([{ objet: 'Transcript', texte, detail: [cause] }])
  })

  it('rien quand la diarisation a réussi', () => {
    expect(reservesDe('Transcript', { diarization_error: null })).toEqual([])
    expect(reservesDe('Transcript', { diarization_error: '  ' })).toEqual([])
  })
})

describe('le lien vers le projet d’un autre compte (constat 6)', () => {
  it.each(['fr', 'en'] as const)('dit « inaccessible », jamais « n’existe plus » (%s)', (lang) => {
    const texte = translate(lang, 'app.scope.urlProjectMissing', { projet: 'P1' })
    expect(texte).toMatch(lang === 'fr' ? /n’est pas accessible depuis ce compte/ : /is not accessible from this account/)
    expect(texte).not.toMatch(/introuvable|no longer exists|n’existe plus/)
  })
})
