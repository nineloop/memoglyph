import { describe, expect, it } from 'vitest'
import { traduireEtape } from '../lib/i18n'
import { CATALOGUES } from '../lib/messages'

/**
 * Traduction des étapes de traitement publiées par le backend.
 *
 * Le backend rend un code, des paramètres et un texte anglais de repli. Ce
 * fichier vérifie les deux chemins — celui du code connu, et surtout celui du
 * code INCONNU, qui est le seul à pouvoir transformer une mise à jour de
 * backend en écran vide.
 */
describe('traduireEtape', () => {
  it('traduit un code connu dans la langue demandée', () => {
    const etape = { code: 'step.diarization', params: {}, message: 'Identifying speakers…' }
    expect(traduireEtape('fr', etape)).toBe('Diarisation (qui parle)…')
    expect(traduireEtape('en', etape)).toBe('Identifying speakers…')
  })

  it('interpole les paramètres du backend', () => {
    const etape = {
      code: 'step.profiling',
      params: { model: 'qwen2.5:7b' },
      message: 'Profiling with qwen2.5:7b…',
    }
    expect(traduireEtape('fr', etape)).toBe('Profilage avec qwen2.5:7b…')
    expect(traduireEtape('en', etape)).toBe('Profiling with qwen2.5:7b…')
  })

  it('rend les états de PREMIER USAGE du modèle (téléchargement / préparation)', () => {
    // La barre ne doit plus paraître figée. Ces états sont ceux
    // affichés dans la ligne d'activité du panneau de traitement (line 610).
    const dl = {
      code: 'step.model.downloading',
      params: { what: 'whisper-large-v3-turbo' },
      message: 'Downloading the whisper-large-v3-turbo model (first use — this can take several minutes)…',
    }
    expect(traduireEtape('fr', dl)).toContain('Téléchargement du modèle whisper-large-v3-turbo')
    expect(traduireEtape('fr', dl)).toContain('premier usage')
    expect(traduireEtape('en', dl)).toContain('Downloading the whisper-large-v3-turbo model')

    const prep = {
      code: 'step.model.preparing',
      params: {},
      message: 'Preparing the local model. First use may require downloading several GB.',
    }
    expect(traduireEtape('fr', prep)).toBe(
      'Préparation du modèle local. Le premier usage peut nécessiter le téléchargement de plusieurs Go.')
    expect(traduireEtape('en', prep)).toBe(
      'Preparing the local model. First use may require downloading several GB.')
  })

  it('rend le repli anglais du backend quand le code est INCONNU', () => {
    // Un backend plus récent émet un code que cette interface ignore. Afficher
    // un vide ou la clé brute ferait passer une mise à jour pour une panne.
    const etape = {
      code: 'step.futur.inconnu',
      params: {},
      message: 'Doing something new…',
    }
    expect(traduireEtape('fr', etape)).toBe('Doing something new…')
    expect(traduireEtape('en', etape)).toBe('Doing something new…')
  })

  it('rend le message tel quel quand il n’y a pas de code', () => {
    // Message d'exception ou texte d'une dépendance : pas de code stable.
    const etape = { code: null, params: {}, message: 'MemoryError: out of memory' }
    expect(traduireEtape('fr', etape)).toBe('MemoryError: out of memory')
  })

  it('ne laisse aucun paramètre non substitué dans les deux langues', () => {
    // `translate` laisse `{nom}` VISIBLE quand un paramètre manque — par
    // conception. Ce test vérifie que les gabarits FR et EN attendent les
    // MÊMES paramètres : un `{model}` oublié côté français serait un trou que
    // seul un écran en français révélerait.
    const cas: { code: string; params: Record<string, string | number> }[] = [
      { code: 'step.profiling', params: { model: 'm' } },
      { code: 'step.transcription', params: { engine: 'whisper' } },
      { code: 'step.upload', params: { provider: 'Mistral' } },
      { code: 'step.model.loading', params: { what: 'embedding' } },
      { code: 'step.model.downloading', params: { what: 'whisper-large-v3-turbo' } },
      { code: 'step.model.preparing', params: {} },
      { code: 'step.diarization.done', params: { count: 3 } },
      { code: 'warn.report.failed', params: { error: 'boom' } },
      { code: 'step.done', params: { name: 'x.md' } },
      { code: 'step.transcription.whisper', params: { language: 'fr' } },
      { code: 'step.transcription.chunk', params: { done: 1, total: 2 } },
      { code: 'step.upload.compressed', params: { before_mb: 120, after_mb: 30 } },
    ]
    for (const { code, params } of cas) {
      for (const lang of ['fr', 'en'] as const) {
        const rendu = traduireEtape(lang, { code, params, message: 'repli' })
        expect(rendu, `${lang} / ${code}`).not.toMatch(/\{\w+\}/)
      }
    }
  })

  it('couvre en français tous les codes que le backend sait émettre', () => {
    // La liste est celle de `core/messages.py`. Elle est recopiée ici parce que
    // rien ne relie les deux langages : un code ajouté côté Python sans clé
    // côté interface s'afficherait en anglais, en silence. Ce test le dit.
    const CODES_BACKEND = [
      'step.audio.prepare',
      'step.audio.ready',
      'step.diarization',
      'step.voiceprints.after_native_diarization',
      'step.profiling',
      'step.markdown',
      'step.report',
      'warn.report.failed',
      'step.done',
      'step.transcription',
      'step.upload',
      'step.model.loading',
      'step.model.downloading',
      'step.model.preparing',
      'step.diarization.done',
      'step.transcription.whisper',
      'step.transcription.chunk',
      'step.transcription.done',
      'step.upload.mistral',
      'step.upload.compressed',
      'step.upload.mistral.retry',
      'step.upload.mistral.pause',
      'step.segments.format',
      'step.transcription.mistral.done',
      'step.diarization.loading',
      'step.diarization.analyzing',
      'step.profiling.running',
      'step.profiling.done',
      'step.profiling.warmup',
      'step.report.chunks',
      'step.report.classify',
      'step.report.extract',
      'step.report.organize',
      'step.report.write',
    ]
    const manquants = CODES_BACKEND.filter((c) => !(`job.${c}` in CATALOGUES.fr))
    expect(manquants).toEqual([])
  })
})
