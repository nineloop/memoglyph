import { describe, expect, it } from 'vitest'
import { reservesDe } from '../lib/reserves'

describe('reservesDe', () => {
  it('ne dit rien quand il n’y a rien à dire', () => {
    expect(reservesDe('Transcript', {})).toEqual([])
  })

  it('ignore une clé présente mais VIDE', () => {
    // Le pipeline écrit `null`, mais une base ancienne peut contenir `[]` —
    // annoncer « 0 réserve » serait un mensonge.
    expect(reservesDe('Transcript', { repetition_loops: [], removed_credits: null })).toEqual([])
  })

  it('accorde le libellé au singulier et au pluriel', () => {
    expect(reservesDe('CR', { removed_speaker_bindings: ['a'] })[0].texte)
      .toBe('1 identité de locuteur retirée')
    expect(reservesDe('CR', { removed_speaker_bindings: ['a', 'b'] })[0].texte)
      .toBe('2 identités de locuteur retirées')
  })

  it('rattache chaque réserve à l’objet qu’elle accuse', () => {
    // `unsupported_numbers` accuse le CR, `repetition_loops` le transcript :
    // les confondre ferait mentir les deux.
    const r = reservesDe('Compte-rendu du 20/07', { unsupported_numbers: ['5'] })
    expect(r[0].objet).toBe('Compte-rendu du 20/07')
  })

  it('conserve le détail pour l’afficher en contexte', () => {
    const r = reservesDe('CR', { removed_speaker_bindings: ['Speaker 3 (X)', 'Speaker 9 (Y)'] })
    expect(r[0].detail).toEqual(['Speaker 3 (X)', 'Speaker 9 (Y)'])
  })

  it('rend les anomalies de citation portées par le Markdown', () => {
    const r = reservesDe('CR', {
      citation_warnings: ['citation altérée à 00:01:02', 'affirmation sans citation'],
    })
    expect(r).toEqual([{
      objet: 'CR',
      texte: '2 anomalies de citation à vérifier',
      detail: ['citation altérée à 00:01:02', 'affirmation sans citation'],
    }])
  })

  it('cumule plusieurs types de réserves', () => {
    const r = reservesDe('T', { repetition_loops: ['a'], repeated_series: ['b'], removed_credits: ['c'] })
    expect(r).toHaveLength(3)
  })
})
