import { describe, expect, it } from 'vitest'
import { etiquetteLocuteur } from '../lib/locuteurGenerique'
import { translate } from '../lib/i18n'

describe('« Speaker N » devient « Locuteur N » à l’affichage seulement', () => {
  const fr = (cle: 'spoken.speakerGeneric', p: { n: string }) => translate('fr', cle, p)
  const en = (cle: 'spoken.speakerGeneric', p: { n: string }) => translate('en', cle, p)

  it('traduit l’étiquette générique, jamais un nom saisi', () => {
    expect(etiquetteLocuteur('Speaker 3', fr)).toBe('Locuteur 3')
    expect(etiquetteLocuteur('Speaker 3', en)).toBe('Speaker 3')
    expect(etiquetteLocuteur('Alice', fr)).toBe('Alice')
    expect(etiquetteLocuteur('Speaker Alice', fr)).toBe('Speaker Alice')
    expect(etiquetteLocuteur('Intervenant Speaker 3', fr)).toBe('Intervenant Speaker 3')
  })
})
