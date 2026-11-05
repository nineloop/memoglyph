import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ListeTraitements, Traitement } from '../api'
import {
  INTERVALLE_ACTIF_MS, INTERVALLE_REPOS_MS, annulable, comptes, demarrerReleve, etatAffiche, intervalleReleve,
  separer, valeurDepuis,
} from '../lib/traitements'

const T = (extra: Partial<Traitement> = {}): Traitement => ({
  id: 'j1', nature: 'transcription', statut: 'running', en_attente: false, progression: 0.4,
  titre: 'visio', projet_id: 'P', projet_nom: 'Réunions AMI', etape: 2, etapes: 5,
  message_code: null, message_params: {}, debut: 1000, meeting: null,
  erreur: null, erreur_code: null, erreur_params: {}, ...extra,
})
const L = (traitements: Traitement[], actifs = 0, attente = 0): ListeTraitements =>
  ({ traitements, actifs, en_attente: attente })

describe('les états affichés et les comptes', () => {
  it('en attente = en_attente ou pending ; un traitement qui attend n\'est pas « en cours »', () => {
    expect(etatAffiche(T({ statut: 'pending' }))).toBe('en_attente')
    expect(etatAffiche(T({ statut: 'running', en_attente: true }))).toBe('en_attente')
    expect(etatAffiche(T())).toBe('en_cours')
    expect(etatAffiche(T({ statut: 'done' }))).toBe('termine')
    expect(etatAffiche(T({ statut: 'error' }))).toBe('echec')
  })

  it('comptes : « N en cours · M en attente », sans parler de ce qui est à zéro', () => {
    expect(comptes(L([], 2, 1))).toEqual({ enCours: 2, enAttente: 1, total: 3 })
    expect(comptes(null)).toEqual({ enCours: 0, enAttente: 0, total: 0 })
  })

  it('separer : actifs d\'abord, puis les terminés récents ; « Tout voir » rend tout', () => {
    const finis = [1, 2, 3, 4, 5, 6, 7].map((i) => T({ id: `f${i}`, statut: 'done' }))
    const liste = L([T({ id: 'a' }), T({ id: 'w', statut: 'pending' }), ...finis], 1, 1)
    const court = separer(liste, false)
    expect(court.actifs.map((t) => t.id)).toEqual(['a', 'w'])
    expect(court.termines).toHaveLength(5)
    expect(separer(liste, true).termines).toHaveLength(7)
  })

  it('valeurDepuis : à l\'instant, minutes, heures', () => {
    expect(valeurDepuis(1000, 1020)).toEqual({ unite: 'now', n: 0 })
    expect(valeurDepuis(1000, 1000 + 4 * 60 + 5)).toEqual({ unite: 'min', n: 4 })
    expect(valeurDepuis(1000, 1000 + 3 * 3600 + 10)).toEqual({ unite: 'h', n: 3 })
    expect(valeurDepuis(1000, 900)).toEqual({ unite: 'now', n: 0 })        // horloge en retard : jamais négatif
  })
})

describe('la relève : 3 s quand quelque chose tourne, 15 s au repos, suspendue onglet masqué', () => {
  let visible = true
  const doc = {
    get visibilityState() { return visible ? 'visible' : 'hidden' },
    addEventListener: vi.fn((_: string, f: () => void) => { ecoute = f }),
    removeEventListener: vi.fn(),
  } as unknown as Document
  let ecoute: () => void = () => {}

  beforeEach(() => { vi.useFakeTimers(); visible = true; ecoute = () => {} })
  afterEach(() => { vi.useRealTimers() })

  it('intervalleReleve : 3 s avec un traitement en cours ou en attente, 15 s sinon', () => {
    expect(intervalleReleve(L([], 1, 0))).toBe(INTERVALLE_ACTIF_MS)
    expect(intervalleReleve(L([], 0, 2))).toBe(INTERVALLE_ACTIF_MS)
    expect(intervalleReleve(L([], 0, 0))).toBe(INTERVALLE_REPOS_MS)
    expect(intervalleReleve(null)).toBe(INTERVALLE_REPOS_MS)
    expect(INTERVALLE_ACTIF_MS).toBe(3000)
    expect(INTERVALLE_REPOS_MS).toBe(15000)
  })

  it('relève immédiatement, puis à 3 s tant que quelque chose tourne', async () => {
    const recuperer = vi.fn(async () => L([T()], 1, 0))
    const releve = demarrerReleve({ recuperer, surListe: () => {}, doc })
    await vi.advanceTimersByTimeAsync(0)
    expect(recuperer).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2999)
    expect(recuperer).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(recuperer).toHaveBeenCalledTimes(2)
    releve.arreter()
  })

  it('ralentit à 15 s dès que plus rien ne tourne', async () => {
    const recuperer = vi.fn(async () => L([], 0, 0))
    const releve = demarrerReleve({ recuperer, surListe: () => {}, doc })
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(14999)
    expect(recuperer).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(recuperer).toHaveBeenCalledTimes(2)
    releve.arreter()
  })

  it('est SUSPENDUE onglet masqué : aucune requête, et reprend tout de suite au retour', async () => {
    const recuperer = vi.fn(async () => L([T()], 1, 0))
    const releve = demarrerReleve({ recuperer, surListe: () => {}, doc })
    await vi.advanceTimersByTimeAsync(0)
    expect(recuperer).toHaveBeenCalledTimes(1)
    visible = false
    await vi.advanceTimersByTimeAsync(60_000)
    expect(recuperer).toHaveBeenCalledTimes(1)            // masqué : plus rien
    visible = true
    ecoute()                                              // visibilitychange
    await vi.advanceTimersByTimeAsync(0)
    expect(recuperer).toHaveBeenCalledTimes(2)            // reprise immédiate, sans attendre 3 s
    releve.arreter()
  })

  it('arreter() ne laisse aucune minuterie (pas de relève doublée sous StrictMode)', async () => {
    const recuperer = vi.fn(async () => L([T()], 1, 0))
    const releve = demarrerReleve({ recuperer, surListe: () => {}, doc })
    await vi.advanceTimersByTimeAsync(0)
    expect(vi.getTimerCount()).toBe(1)             // la prochaine relève est planifiée…
    releve.arreter()
    expect(vi.getTimerCount()).toBe(0)             // …et arreter() l'annule tout de suite
    await vi.advanceTimersByTimeAsync(60_000)
    expect(recuperer).toHaveBeenCalledTimes(1)
  })

  it('une erreur réseau garde la dernière liste et réessaie au rythme du repos', async () => {
    const surListe = vi.fn()
    const recuperer = vi.fn()
      .mockResolvedValueOnce(L([T()], 1, 0))
      .mockRejectedValueOnce(new Error('réseau'))
      .mockResolvedValue(L([], 0, 0))
    const releve = demarrerReleve({ recuperer, surListe, doc })
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(3000)
    expect(surListe).toHaveBeenCalledTimes(1)             // l'échec n'a rien effacé
    await vi.advanceTimersByTimeAsync(15000)
    expect(recuperer).toHaveBeenCalledTimes(3)
    expect(surListe).toHaveBeenCalledTimes(2)
    releve.arreter()
  })

  it('rafraichir() relève tout de suite, sans doubler la boucle', async () => {
    const recuperer = vi.fn(async () => L([T()], 1, 0))
    const releve = demarrerReleve({ recuperer, surListe: () => {}, doc })
    await vi.advanceTimersByTimeAsync(0)
    releve.rafraichir()
    await vi.advanceTimersByTimeAsync(0)
    expect(recuperer).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(3000)
    expect(recuperer).toHaveBeenCalledTimes(3)            // une seule boucle, pas deux
    releve.arreter()
  })
})

describe('un traitement annulé', () => {
  const annule = (extra: Partial<Traitement> = {}) => T({
    statut: 'error', erreur: 'Traitement annulé.', erreur_code: 'step.error.cancelled', ...extra })

  it('un échec de code « annulé » a son propre état affiché ; tout autre échec reste un échec', () => {
    expect(etatAffiche(annule())).toBe('annule')
    expect(etatAffiche(T({ statut: 'error', erreur_code: 'step.error.waitTooLong' }))).toBe('echec')
    expect(etatAffiche(T({ statut: 'error', erreur_code: null }))).toBe('echec')
    // Un traitement qui n'est pas en échec n'est jamais « annulé », quel que soit son code.
    expect(etatAffiche(T({ statut: 'running', erreur_code: 'step.error.cancelled' }))).toBe('en_cours')
  })

  it('separer le range dans les terminés, pas dans les actifs', () => {
    const liste = L([T({ id: 'a' }), annule({ id: 'x' }), T({ id: 'f', statut: 'done' })], 1, 0)
    const { actifs, termines } = separer(liste, false)
    expect(actifs.map((t) => t.id)).toEqual(['a'])
    expect(termines.map((t) => t.id)).toEqual(['x', 'f'])
  })

  it('annulable : seulement « en cours de traitement » ET en attente de place (jamais pending, jamais fini)', () => {
    expect(annulable(T({ statut: 'running', en_attente: true }))).toBe(true)
    expect(annulable(T({ statut: 'pending', en_attente: true }))).toBe(false)     // en réception : l'API refuse (409)
    expect(annulable(T({ statut: 'running', en_attente: false }))).toBe(false)
    expect(annulable(T({ statut: 'done' }))).toBe(false)
    expect(annulable(annule())).toBe(false)
  })
})
