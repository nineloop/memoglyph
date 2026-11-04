import { afterEach, expect, it, vi } from 'vitest'

import { currentLang, detectLang, getLangOverride, pluralKey, setLangOverride, translate } from '../lib/i18n'
import { CATALOGUES } from '../lib/messages'
import type { MessageKey } from '../lib/messages'

afterEach(() => vi.unstubAllGlobals())


// ---------- Détection ----------

it('reconnaît une sous-étiquette régionale', () => {
  expect(detectLang(['fr-CA'])).toBe('fr')
  expect(detectLang(['en-GB'])).toBe('en')
})

it('prend la PREMIÈRE préférence connue, pas la première tout court', () => {
  expect(detectLang(['pt-BR', 'en-US', 'fr'])).toBe('en')
})

it('retombe sur le français quand aucune langue n’est reconnue', () => {
  expect(detectLang(['ja', 'ko'])).toBe('fr')
  expect(detectLang([])).toBe('fr')
})


// ---------- Surcharge persistée ----------

it('le choix explicite prime sur le navigateur', () => {
  // Le `setup.ts` fixe une langue de référence pour tous les tests : on efface
  // ici la surcharge, sinon on mesurerait cette préparation, pas la détection.
  localStorage.removeItem('memoglyph.lang')
  vi.stubGlobal('navigator', { languages: ['en-US'], language: 'en-US' })
  expect(currentLang()).toBe('en')

  setLangOverride('fr')
  expect(getLangOverride()).toBe('fr')
  expect(currentLang()).toBe('fr')
})

it('ignore une valeur de stockage qui n’est pas une langue connue', () => {
  localStorage.setItem('memoglyph.lang', 'kl')
  expect(getLangOverride()).toBeNull()
  // …et la détection reprend la main plutôt que de propager la valeur douteuse.
  vi.stubGlobal('navigator', { languages: ['en'], language: 'en' })
  expect(currentLang()).toBe('en')
})


// ---------- Interpolation ----------

it('remplace les paramètres nommés', () => {
  expect(translate('fr', 'transcribe.profile.detail', { llm: 'qwen3' }))
    .toBe('Résumé et profils via qwen3')
  expect(translate('en', 'transcribe.profile.detail', { llm: 'qwen3' }))
    .toBe('Summary and profiles via qwen3')
})

it('laisse un paramètre manquant VISIBLE plutôt que d’ouvrir un trou muet', () => {
  expect(translate('fr', 'transcribe.profile.detail', {})).toContain('{llm}')
})


// ---------- Cohérence des catalogues ----------

const CLES = Object.keys(CATALOGUES.fr) as MessageKey[]

it('les deux catalogues portent exactement les mêmes clés', () => {
  expect(Object.keys(CATALOGUES.en).sort()).toEqual(CLES.slice().sort())
})

/**
 * Une traduction qui laisse tomber un `{param}` décrirait MOINS que l'autre :
 * le nom du modèle ou le moteur disparaîtrait de la phrase anglaise sans que
 * rien ne le signale — la version anglaise redeviendrait décorative, exactement
 * le défaut déjà rencontré côté français.
 */
function parametres(gabarit: string): string[] {
  return [...gabarit.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
}

it('aucune traduction ne perd un paramètre en route', () => {
  const ecarts = CLES
    .map((cle) => ({
      cle,
      fr: parametres(CATALOGUES.fr[cle]),
      en: parametres(CATALOGUES.en[cle]),
    }))
    .filter((ligne) => ligne.fr.join(',') !== ligne.en.join(','))

  expect(ecarts).toEqual([])
})

it('le contrôle précédent porte bien sur des messages paramétrés', () => {
  // Sans cette garde, il resterait vert sur un catalogue sans aucun paramètre.
  const parametres_ = CLES.filter((cle) => parametres(CATALOGUES.fr[cle]).length > 0)
  expect(parametres_.length).toBeGreaterThan(8)
})

// ---------- Pluriels ----------

/**
 * Chaque compteur migré s'exprime par une paire `.one`/`.other`, jamais une
 * clé isolée : une `.one` sans `.other` (ou l'inverse) ferait planter au rendu
 * dès que le nombre bascule de l'autre côté. On vérifie donc la complétude des
 * paires DANS LES DEUX catalogues à la fois.
 */
it('toute clé pluralisée forme une paire .one/.other complète', () => {
  // Un COMPTEUR interpole `{n}` ; `spoken.other` (« Autre langue… ») porte le
  // suffixe `.other` sans être un pluriel — on ne le confond pas avec une paire.
  const orphelines = CLES
    .filter((cle) => (cle.endsWith('.one') || cle.endsWith('.other')) && CATALOGUES.fr[cle].includes('{n}'))
    .filter((cle) => {
      const jumelle = cle.endsWith('.one')
        ? (cle.slice(0, -4) + '.other') as MessageKey
        : (cle.slice(0, -6) + '.one') as MessageKey
      return !(jumelle in CATALOGUES.fr)
    })
  expect(orphelines).toEqual([])
})

/**
 * `pluralKey` porte la règle FRANÇAISE partagée par tout le catalogue : seul
 * `n > 1` prend le pluriel. 0 et 1 restent au singulier — c'est ce qui fait
 * « 0 document » et « 1 document ».
 */
it('pluralKey applique la règle française (seul n>1 est pluriel)', () => {
  expect(pluralKey('meeting.speakers.count', 0)).toBe('meeting.speakers.count.one')
  expect(pluralKey('meeting.speakers.count', 1)).toBe('meeting.speakers.count.one')
  expect(pluralKey('meeting.speakers.count', 2)).toBe('meeting.speakers.count.other')
  expect(translate('fr', pluralKey('meeting.speakers.count', 3), { n: 3 })).toBe('3 locuteurs')
  expect(translate('en', pluralKey('meeting.speakers.count', 1), { n: 1 })).toBe('1 speaker')
})

it('aucun message ne reste identique par oubli de traduction', () => {
  // Certains libellés SONT identiques dans les deux langues : noms propres,
  // sigles, listes d'unités techniques. On les liste explicitement plutôt que
  // de tolérer l'égalité en silence — un ajout non traduit doit se voir.
  const identiquesAssumes: MessageKey[] = [
    'finops.transcription', 'finops.audio',
    // « Usage » et « Audio » s'écrivent à l'identique dans les deux langues.
    'app.nav.usage', 'usage.kpi.audio',
    'app.lang.fr', 'app.lang.en', 'app.status.llmUnknown',
    'app.nav.ask', 'app.nav.maintenance',
    // Tranche B : « Transcription » et « Archives » s'écrivent à l'identique.
    'traitements.nature.transcription', 'traitements.nature.archives',
    'app.provider.ollamaLocal', 'app.provider.mlxLocal', 'app.provider.mistralCloud',
    'app.provider.openaiCloud', 'app.provider.anthropicCloud',
    'app.provider.openrouter', 'app.provider.groq',      // noms propres (chantier 006)
    'spoken.codePlaceholder',
    'diar.engine.cloud', 'diar.engine.local',
    'transcribe.flow.options', 'transcribe.options.title',
    'transcribe.expert.stt', 'transcribe.expert.llm',
    'transcribe.mode.sideMistral',
    'sensitivity.standard',
    'timeline.stt', 'timeline.archive',
    // « passage » et « document » s'écrivent à l'identique dans les deux langues.
    'search.count.passages.one', 'search.count.passages.other',
    'search.count.documents.one', 'search.count.documents.other',
    // Surfaces migrées : noms propres, emprunts (« Transcript », « Keynote »),
    // et noms de compte identiques dans les deux langues (« occurrence(s) »,
    // « passage(s) », « versions »). Listés, jamais tolérés en silence.
    'common.docKind.transcript',
    'dedup.versions',
    'library.badge.oneTranscript', 'library.badge.versions',
    'maint.card.documents',
    'maint.card.semantic.detail.one', 'maint.card.semantic.detail.other',
    'maint.card.voiceprints.detail.one', 'maint.card.voiceprints.detail.other',
    'maint.diag.title', 'maint.tab.backfill', 'maint.title',
    'meeting.fact.date', 'meeting.facts.mode.cloud', 'meeting.facts.mode.local', 'meeting.facts.source',
    'meeting.facts.type.action', 'meeting.info.source', 'meeting.tab.versions',
    // « Capture » et « replay » s'écrivent à l'identique dans les deux langues.
    'meeting.info.capture', 'meeting.info.capture.replay',
    'meeting.versions.transcripts',
    'reader.backTranscript',
    'report.genre.interview', 'report.genre.keynote',
    'voices.occurrences.count.one', 'voices.occurrences.count.other',
    'voices.occurrences.title',
  ]
  const identiques = CLES.filter((cle) => CATALOGUES.fr[cle] === CATALOGUES.en[cle])

  expect(identiques.sort()).toEqual(identiquesAssumes.sort())
})


// ---------- Chantier 003 : les nouvelles surfaces existent dans les DEUX langues ----------

it('chaque nouvelle surface du chantier 003 a des libellés en français ET en anglais', () => {
  const PREFIXES = ['nav.', 'reglages.', 'destination.', 'barre.', 'projets.', 'traitements.']
  for (const prefixe of PREFIXES) {
    const fr = Object.keys(CATALOGUES.fr).filter((k) => k.startsWith(prefixe))
    const en = Object.keys(CATALOGUES.en).filter((k) => k.startsWith(prefixe))
    expect(fr.length, `aucune clé « ${prefixe} » en français`).toBeGreaterThan(0)
    expect(en.sort()).toEqual(fr.sort())
  }
})
