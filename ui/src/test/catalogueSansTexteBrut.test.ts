/**
 * Aucune phrase d'erreur du catalogue ne laisse un trou pour le texte brut d'une exception ou du serveur. Les rares phrases qui en
 * gardent un sont nommées ici, chacune avec sa raison : ajouter une phrase à trou d'erreur sans l'inscrire fait rougir ce test.
 */
import { describe, expect, it } from 'vitest'

import { CATALOGUES } from '../lib/messages'

const TROU_DE_TEXTE_BRUT = /\{(error|erreur|detail|cause|reason|raison|message)\}/

/**
 * Les phrases qui gardent un trou, et pourquoi. Autre exception nommée, sans clé de catalogue donc absente de cette liste : le
 * texte d'un échec de flux de transcription sans code (un serveur plus ancien), affiché tel quel dans `TranscribePanel`.
 */
const AUTORISEES: Record<string, string> = {
  // Messages d'étape produits par le traitement lui-même (déjà codés), qui citent la cause rapportée par le pipeline.
  'job.warn.report.retry': 'message d’étape du traitement',
  'job.warn.profiling.failed': 'message d’étape du traitement',
  'job.warn.diarization.failed': 'message d’étape du traitement',
  'job.warn.report.failed': 'message d’étape du traitement',
  // Diagnostic de la Maintenance : un écran d'administrateur, où la cause est ce que l'on vient lire.
  'maint.diag.corpusFrozen.detail': 'diagnostic de la Maintenance (administrateur)',
  'maint.diag.analysisDown.detail': 'diagnostic de la Maintenance (administrateur)',
  'maint.corpus.reindexFailed': 'diagnostic de la Maintenance (administrateur)',
  // Pas des erreurs : le motif d'un fait périmé et la raison d'un doublon sont des libellés de domaine.
  'meeting.facts.stale.reason.other': 'libellé de domaine, pas une erreur',
  'dedup.keep': 'libellé de domaine, pas une erreur',
  // Refus de l'envoi : le trou ne reçoit qu'une phrase du CATALOGUE (code connu) ; sans code connu, c'est la phrase générale, jamais celle du serveur.
  'envoi.refus.detail': 'reçoit une phrase du catalogue, jamais le texte du serveur',
  // Échec d'une relance des faits : le texte du serveur n'est montré que sans code (un serveur plus ancien n'en envoie pas ; un test l'exige).
  'meeting.facts.rerun.error': 'serveur plus ancien, sans code : le texte du serveur reste affiché',
  // À retirer avec le remplacement de l'alerte des doublons.
}

describe('le catalogue et le texte brut', () => {
  const cles = Object.keys(CATALOGUES.fr) as Array<keyof typeof CATALOGUES.fr>

  it('toute phrase à trou de texte brut est dans la liste des exceptions nommées, dans les deux langues', () => {
    const inattendues: string[] = []
    for (const lang of ['fr', 'en'] as const) {
      for (const cle of cles) {
        if (TROU_DE_TEXTE_BRUT.test(CATALOGUES[lang][cle]) && !(cle in AUTORISEES)) inattendues.push(`${lang} ${cle}`)
      }
    }
    expect(inattendues).toEqual([])
  })

  it('une exception nommée garde bien son trou : sinon elle sort de la liste', () => {
    const perimees = Object.keys(AUTORISEES).filter((cle) => !TROU_DE_TEXTE_BRUT.test(CATALOGUES.fr[cle as keyof typeof CATALOGUES.fr]))
    expect(perimees).toEqual([])
  })
})
