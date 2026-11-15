/**
 * Règle d'écriture de l'interface : phrases courtes, vouvoiement ou infinitif, aucun jargon
 * d'implémentation visible, « Local » et « En ligne » pour dire où se fait un traitement.
 *
 * Ce test lit les DEUX catalogues. Les exceptions sont explicites, une raison chacune : ajouter une chaîne qui
 * enfreint la règle, c'est la faire échouer ici.
 */
import { describe, expect, it } from 'vitest'
import { CATALOGUES } from '../lib/messages'

// Préfixes ou clés exclus, avec la raison.
const EXCEPTIONS: [RegExp, string][] = [
  [/^auth\.login\.forgot$/, 'la commande se lance vraiment sur la machine qui héberge Memoglyph'],
]

const TUTOIEMENT = /(?<![\p{L}])(tu|toi|te|ton|ta|tes)(?![\p{L}])/iu
// « Copie audio conservée » est un nom (le titre de la carte de Maintenance), pas l'impératif de « copier ».
const IMPERATIF_TU = /(?:^|[.:;—–(]\s*)(Dépose|Clique|Transcris|Pose|Décris|Vérifie|Observe|Lance|Arbitre|Choisis|Saisis|Ouvre|Regarde|Suis|Essaie|Réessaie|Attends|Utilise|Consulte|Ajoute|Copie(?! audio)|Colle|Entre|Rends|Cherche|Renseigne|Retire|Garde|Vois|Fais|Prends|Mets|Tape|Écris|Lis)(?![\p{L}])/u
const JARGON: [RegExp, string][] = [
  [/(?<![\p{L}])LLM(?![\p{L}])/u, 'LLM'], [/(?<![\p{L}])DIAR(?![\p{L}])/u, 'DIAR'], [/Backfill/i, 'Backfill'],
  [/Map-Reduce/i, 'Map-Reduce'], [/HF_TOKEN/, 'HF_TOKEN'], [/_API_KEY/, '_API_KEY'], [/\.env(?![\p{L}])/u, '.env'],
  [/(?<![\p{L}])portée/iu, 'portée'], [/(?<![\p{L}])scope(?![\p{L}])/iu, 'scope'], [/(?<![\p{L}])CR(?![\p{L}])/u, 'CR'], [/telemetry/i, 'telemetry'], [/voice[- ]identit/i, 'voice identity'],
  [/(?<![\p{L}])empreintes?(?![\p{L}])/iu, 'empreinte'], [/fingerprints?|voiceprints?/i, 'fingerprint'],
  [/artefacts?|artifacts?/i, 'artefact'], [/diaris/i, 'diaris-'], [/diariz/i, 'diariz-'], [/profilage|profiling|profilé|profiled/i, 'profilage'],
]
const MOT_FR_SEULEMENT: [RegExp, string][] = [
  [/(?<![\p{L}])Speaker(?![\p{L}])/u, 'Speaker'],
  // Un mot par notion : « compte rendu », sans trait d'union, au singulier comme au pluriel.
  [/comptes?-rendus?/i, 'compte-rendu'],
  [/télémétrie/i, 'télémétrie'], [/identités? vocales?/i, 'identité vocale'],
]
const LIEU: [RegExp, string][] = [
  [/cette machine/i, 'cette machine'], [/ce Mac(?![\p{L}])/u, 'ce Mac'], [/this machine/i, 'this machine'], [/this Mac/i, 'this Mac'],
]

function dansPerimetre(cle: string) {
  return !EXCEPTIONS.some(([re]) => re.test(cle))
}

describe('règle d’écriture : catalogues', () => {
  it('les exceptions sont connues et justifiées', () => {
    expect(EXCEPTIONS.every(([, raison]) => raison.length > 20)).toBe(true)
  })

  for (const langue of ['fr', 'en'] as const) {
    it(`${langue} : ni jargon d’implémentation, ni « cette machine », ni « ce Mac »`, () => {
      const fautes: string[] = []
      for (const [cle, valeur] of Object.entries(CATALOGUES[langue])) {
        if (!dansPerimetre(cle)) continue
        const regles = [...JARGON, ...LIEU, ...(langue === 'fr' ? MOT_FR_SEULEMENT : [])]
        const texte = valeur.replace(/\{[^}]*\}/g, '')
        for (const [re, nom] of regles) if (re.test(texte)) fautes.push(`${cle} : « ${nom} »`)
      }
      expect(fautes).toEqual([])
    })
  }

  it('fr : ni tutoiement ni impératif de deuxième personne du singulier', () => {
    const fautes: string[] = []
    for (const [cle, valeur] of Object.entries(CATALOGUES.fr)) {
      if (!dansPerimetre(cle)) continue
      if (TUTOIEMENT.test(valeur) || IMPERATIF_TU.test(valeur)) fautes.push(`${cle} : ${valeur.slice(0, 60)}`)
    }
    expect(fautes).toEqual([])
  })
})
