/**
 * Maintenance, onglet Reprise : la copie audio conservée porte UN seul nom dans la carte, le bouton et les messages de suivi.
 * (« Archives fidèles » et « Retraitement futur » disaient la même chose avec d'autres mots, et l'un d'eux n'a pas de sens pour un lecteur.)
 */
import { describe, expect, it } from 'vitest'
import { CATALOGUES } from '../lib/messages'

const CLES = [
  'maint.backfill.faithful.eyebrow', 'maint.backfill.faithful.title', 'maint.backfill.faithful.none',
  'maint.backfill.faithful.action', 'maint.msg.faithfulStarting', 'maint.msg.faithfulDone',
] as const

describe('la copie audio conservée', () => {
  it('a son nom en français et en anglais', () => {
    expect(CATALOGUES.fr['maint.backfill.faithful.eyebrow']).toBe('Audio d’origine')
    expect(CATALOGUES.fr['maint.backfill.faithful.title']).toBe('Copie audio conservée')
    expect(CATALOGUES.en['maint.backfill.faithful.eyebrow']).toBe('Original audio')
    expect(CATALOGUES.en['maint.backfill.faithful.title']).toBe('Kept audio copy')
  })

  it('n’emploie plus les anciens mots, ni « archive », dans la carte, le bouton et le suivi', () => {
    for (const langue of ['fr', 'en'] as const) {
      for (const cle of CLES) {
        expect(CATALOGUES[langue][cle], `${langue} ${cle}`).not.toMatch(/archive|fid[eè]le|faithful|retraitement|reprocessing/i)
      }
    }
  })

  it('dit « copie » dans le bouton et les deux messages de suivi', () => {
    expect(CATALOGUES.fr['maint.backfill.faithful.action']).toBe('Créer les copies ({n})')
    expect(CATALOGUES.fr['maint.msg.faithfulStarting']).toBe('Préparation des copies audio conservées…')
    expect(CATALOGUES.fr['maint.msg.faithfulDone']).toBe('Copies audio conservées terminées.')
    expect(CATALOGUES.en['maint.backfill.faithful.action']).toBe('Create the copies ({n})')
    expect(CATALOGUES.en['maint.msg.faithfulStarting']).toBe('Preparing the kept audio copies…')
    expect(CATALOGUES.en['maint.msg.faithfulDone']).toBe('Kept audio copies complete.')
  })
})
