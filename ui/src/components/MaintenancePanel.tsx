import { useCallback, useEffect, useState } from 'react'
import type { MaintenanceStatus } from '../api'
import {
  getMaintenanceStatus, getTrash, reindexMaintenance, startBackfill,
  startFaithfulBackfill, streamJob,
} from '../api'
import { DedupPanel } from './DedupPanel'
import { TrashPanel } from './TrashPanel'
import { Icon } from './Icon'
import { ScopeControl } from './ScopeControl'
import { useLatestRequest } from '../lib/latestRequest'
import { useLang } from '../lib/langContext'
import { pluralKey } from '../lib/i18n'

type Props = {
  viewScope: string
  scopeLabel: string
  /** Section d'arrivée, imposée par la navigation. Défaut : le diagnostic. */
  initialSection?: Section
  /** Remonte un changement (restauration, purge) pour rafraîchir le compteur. */
  onChanged?: () => void
  /** Diagnostic et retraitements : administrateurs seulement. Doublons
   *  et corbeille restent à chacun, bornés à ses projets. */
  admin?: boolean
}
type Section = 'status' | 'backfill' | 'duplicates' | 'trash'

export function MaintenancePanel({
  viewScope, scopeLabel, initialSection = 'status', onChanged, admin = true,
}: Props) {
  const { t } = useLang()
  const depart: Section = admin || initialSection === 'trash' || initialSection === 'duplicates'
    ? initialSection : 'duplicates'
  const [section, setSection] = useState<Section>(depart)
  const [status, setStatus] = useState<MaintenanceStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<'semantic' | 'voices' | 'backfill' | 'faithful' | null>(null)
  const [message, setMessage] = useState('')
  const [trashCount, setTrashCount] = useState(0)
  const [beginLoad, isCurrentLoad] = useLatestRequest()

  const allProjects = viewScope === 'all'

  const load = useCallback(async () => {
    const request = beginLoad()
    setLoading(true); setError('')
    try {
      const [value, trash] = await Promise.all([
        admin ? getMaintenanceStatus(viewScope) : Promise.resolve(null),
        getTrash(viewScope).catch(() => []),
      ])
      if (isCurrentLoad(request)) {
        setStatus(value)
        setTrashCount(trash.length)
      }
    } catch (cause) {
      if (isCurrentLoad(request)) setError(String(cause))
    } finally {
      if (isCurrentLoad(request)) setLoading(false)
    }
  }, [admin, beginLoad, isCurrentLoad, viewScope])
  // La section de DÉPART vient de la navigation, plus d'un « status » figé.
  //
  // « Corbeille » et « Doublons » ont maintenant leur propre entrée : arriver
  // par elles et atterrir sur le diagnostic annulerait tout le gain. C'est ce
  // que faisait déjà « Comparer les versions » depuis une réunion — un
  // troisième clic restait nécessaire.
  useEffect(() => { setSection(depart); setMessage(''); load() },
            [load, depart])

  async function rebuild(kind: 'semantic' | 'voices') {
    setBusy(kind); setError(''); setMessage('')
    try {
      const result = await reindexMaintenance(kind, viewScope)
      const count = result.indexed_files ?? result.indexed ?? 0
      const label = kind === 'semantic' ? t('maint.reindexed.semantic') : t('maint.reindexed.voices')
      setMessage(t(pluralKey('maint.reindexed.done', count), { label, n: count }))
      await load()
    } catch (cause) {
      setError(String(cause))
    } finally {
      setBusy(null)
    }
  }

  async function runBackfill() {
    if (!status?.backfill_eligible.length || busy) return
    setBusy('backfill'); setError(''); setMessage(t('maint.msg.backfillStarting'))
    try {
      const { job_id } = await startBackfill(viewScope)
      streamJob(job_id, {
        onProgress: (_progress, detail) => setMessage(detail),
        onDone: () => {
          setBusy(null); setMessage(t('maint.msg.backfillDone')); load()
        },
        onError: (detail) => { setBusy(null); setError(detail) },
      })
    } catch (cause) {
      setBusy(null); setError(String(cause))
    }
  }

  async function runFaithfulBackfill() {
    if (!status?.faithful_backfill_eligible.length || busy) return
    setBusy('faithful'); setError(''); setMessage(t('maint.msg.faithfulStarting'))
    try {
      const { job_id } = await startFaithfulBackfill(viewScope)
      streamJob(job_id, {
        onProgress: (_progress, detail) => setMessage(detail),
        onDone: () => {
          setBusy(null); setMessage(t('maint.msg.faithfulDone')); load()
        },
        onError: (detail) => { setBusy(null); setError(detail) },
      })
    } catch (cause) {
      setBusy(null); setError(String(cause))
    }
  }

  const semanticMissing = status ? Math.max(0, status.documents - status.semantic_documents) : 0
  const voiceMissing = status ? Math.max(0, status.diarized_documents - status.voice_documents) : 0
  // `null` = rien à signaler ; sinon une phrase prête à afficher. Deux causes
  // distinctes gèlent l'index, et l'utilisateur doit savoir laquelle : le corpus
  // est invalide MAINTENANT (audit vivant), ou la réindexation du démarrage a
  // échoué pour une autre raison (base verrouillée, disque…).
  // Analyses tombées : sans ça, l'écran afficherait « aucun écart » alors
  // qu'il n'a simplement pas pu regarder.
  const analysesKO = Object.entries(status?.analyses_indisponibles || {})

  const corpusFige: string | null = (() => {
    const corpus = status?.corpus
    if (!corpus) return null
    if (corpus.ok === false) {
      // Les clés de cause et les noms de fichiers viennent du backend : données,
      // non traduites. Seul le cadre (« Cause non déterminée ») est localisé.
      const details = Object.entries(corpus.issues || {})
        .map(([cause, fichiers]) => `${cause} : ${fichiers.join(', ')}`)
      if (details.length) return `${details.join(' — ')}.`
      return corpus.erreur ? `${corpus.erreur}.` : t('maint.corpus.undetermined')
    }
    if (corpus.reindexation_demarrage_ok === false) {
      return t('maint.corpus.reindexFailed', {
        detail: corpus.reindexation_demarrage_detail || t('maint.corpus.reindexFailed.unknown'),
      })
    }
    return null
  })()
  const backfillCount = status
    ? status.backfill_eligible.length + status.faithful_backfill_eligible.length
    : 0

  return (
    <section className="card maintenance-workspace">
      <div className="workspace-heading">
        <div>
          <span className="action-scope">{t('maint.eyebrow')}</span>
          <h1>{t('maint.title')}</h1>
          <p>{t('maint.subtitle')}</p>
        </div>
        <button className="btn" onClick={load} disabled={loading}><Icon name="refresh" /> {t('common.refresh')}</button>
      </div>

      <ScopeControl
        allProjects={allProjects}
        canToggle={false}
        scopeLabel={scopeLabel}
        onToggle={() => {}}
        detail={t('maint.scope.detail')}
      />

      <div className="maintenance-tabs" role="tablist" aria-label={t('maint.tabs.aria')}>
        {admin && (
          <>
            <button role="tab" aria-selected={section === 'status'} className={section === 'status' ? 'on' : ''} onClick={() => setSection('status')}>
              <Icon name="settings" /> {t('maint.tab.status')}
            </button>
            <button role="tab" aria-selected={section === 'backfill'} className={section === 'backfill' ? 'on' : ''} onClick={() => setSection('backfill')}>
              <Icon name="refresh" /> {t('maint.tab.backfill')} {backfillCount ? <span>{backfillCount}</span> : null}
            </button>
          </>
        )}
        <button role="tab" aria-selected={section === 'duplicates'} className={section === 'duplicates' ? 'on' : ''} onClick={() => setSection('duplicates')}>
          <Icon name="report" /> {t('maint.tab.duplicates')} {status?.duplicate_groups ? <span>{status.duplicate_groups}</span> : null}
        </button>
        <button role="tab" aria-selected={section === 'trash'} className={section === 'trash' ? 'on' : ''} onClick={() => setSection('trash')}>
          <Icon name="trash" /> {t('maint.tab.trash')} {trashCount ? <span>{trashCount}</span> : null}
        </button>
      </div>

      {error && <p className="error"><Icon name="error" /> {error}</p>}
      {message && <p className="maintenance-message" role="status"><Icon name="check" /> {message}</p>}

      {section === 'status' && (
        loading || !status ? <p className="muted">{t('maint.loadingDiag')}</p> : (
          <div className="maintenance-section" role="tabpanel">
            <div className="diagnostic-grid">
              <article><span>{t('maint.card.documents')}</span><strong>{status.documents}</strong><small>{t('maint.card.documents.detail')}</small></article>
              <article className={semanticMissing ? 'needs-attention' : ''}><span>{t('maint.card.semantic')}</span><strong>{status.semantic_documents}/{status.documents}</strong><small>{t(pluralKey('maint.card.semantic.detail', status.semantic_chunks), { n: status.semantic_chunks })}</small></article>
              <article className={voiceMissing ? 'needs-attention' : ''}><span>{t('maint.card.voiceprints')}</span><strong>{status.voice_documents}/{status.diarized_documents}</strong><small>{t(pluralKey('maint.card.voiceprints.detail', status.voice_occurrences), { n: status.voice_occurrences })}</small></article>
              <article className={status.duplicate_groups ? 'needs-attention' : ''}><span>{t('maint.card.duplicates')}</span><strong>{status.duplicate_groups}</strong><small>{t(pluralKey('maint.card.duplicates.detail', status.duplicate_groups))}</small></article>
            </div>

            <div className="diagnostic-list">
              <h2>{t('maint.diag.title')}</h2>
              {corpusFige === null && analysesKO.length === 0 && semanticMissing === 0 && voiceMissing === 0 && status.duplicate_groups === 0
                ? <p className="diagnostic-ok"><Icon name="check" /> {t('maint.diag.ok')}</p>
                : (
                  <ul>
                    {/* En TÊTE : un corpus non conforme bloque TOUTE réindexation
                        de bibliothèque, ce qui rend les autres diagnostics trompeurs.
                        L'échec ne partait qu'en `print` au démarrage. */}
                    {corpusFige !== null && (
                      <li>
                        <Icon name="alert" />
                        <span>
                          <strong>{t('maint.diag.corpusFrozen.title')}</strong>
                          <small>{t('maint.diag.corpusFrozen.detail', { cause: corpusFige })}</small>
                        </span>
                      </li>
                    )}
                    {semanticMissing > 0 && <li><Icon name="alert" /><span><strong>{t(pluralKey('maint.diag.semanticMissing', semanticMissing), { n: semanticMissing })}</strong><small>{t('maint.diag.semanticMissing.detail')}</small></span></li>}
                    {voiceMissing > 0 && <li><Icon name="alert" /><span><strong>{t(pluralKey('maint.diag.voiceMissing', voiceMissing), { n: voiceMissing })}</strong><small>{t('maint.diag.voiceMissing.detail', { n: status.backfill_eligible.length })}</small></span></li>}
                    {status.duplicate_groups > 0 && <li><Icon name="alert" /><span><strong>{t(pluralKey('maint.diag.duplicates', status.duplicate_groups), { n: status.duplicate_groups })}</strong><small>{t('maint.diag.duplicates.detail')}</small></span></li>}
                  </ul>
                )}
                    {analysesKO.map(([analyse, cause]) => (
                      <li key={analyse}>
                        <Icon name="alert" />
                        <span>
                          <strong>{t('maint.diag.analysisDown.title', { analyse })}</strong>
                          <small>{t('maint.diag.analysisDown.detail', { cause })}</small>
                        </span>
                      </li>
                    ))}
            </div>

            <div className="index-actions">
              <article>
                <div><span className="action-scope">{t('maint.index.semantic.eyebrow')}</span><h2>{t('maint.index.semantic.title')}</h2><p>{t('maint.index.semantic.detail')}</p></div>
                <button className="btn accent" onClick={() => rebuild('semantic')} disabled={Boolean(busy)}>
                  {busy === 'semantic' ? t('maint.index.reindexing') : t('maint.index.semantic.action')}
                </button>
              </article>
              <article>
                <div><span className="action-scope">{t('maint.index.voices.eyebrow')}</span><h2>{t('maint.index.voices.title')}</h2><p>{t('maint.index.voices.detail')}</p></div>
                <button className="btn" onClick={() => rebuild('voices')} disabled={Boolean(busy)}>
                  {busy === 'voices' ? t('maint.index.reindexing') : t('maint.index.voices.action')}
                </button>
              </article>
            </div>
          </div>
        )
      )}

      {section === 'backfill' && (
        <div className="maintenance-section backfill-section" role="tabpanel">
          <div className="maintenance-callout">
            <Icon name="volume" />
            <div><h2>{t('maint.backfill.callout.title')}</h2><p>{t('maint.backfill.callout.detail')}</p></div>
          </div>
          {!status || loading ? <p className="muted">{t('maint.backfill.analyzing')}</p> : (
            <div className="index-actions">
              <article>
                <div>
                  <span className="action-scope">{t('maint.backfill.voices.eyebrow')}</span>
                  <h2>{t('maint.backfill.voices.title')}</h2>
                  <p>{t('maint.backfill.voices.detail')}</p>
                  {status.backfill_eligible.length === 0
                    ? <p className="diagnostic-ok"><Icon name="check" /> {t('maint.backfill.voices.none')}</p>
                    : <div className="backfill-targets"><strong>{t(pluralKey('maint.backfill.eligible', status.backfill_eligible.length), { n: status.backfill_eligible.length })}</strong><ul>{status.backfill_eligible.map((name) => <li key={name}>{name}</li>)}</ul></div>}
                </div>
                <button className="btn" onClick={runBackfill} disabled={Boolean(busy) || status.backfill_eligible.length === 0}>
                  {busy === 'backfill' ? t('maint.backfill.voices.running') : t('maint.backfill.voices.action', { n: status.backfill_eligible.length })}
                </button>
              </article>
              <article>
                <div>
                  <span className="action-scope">{t('maint.backfill.faithful.eyebrow')}</span>
                  <h2>{t('maint.backfill.faithful.title')}</h2>
                  <p>{t('maint.backfill.faithful.detail')}</p>
                  {status.faithful_backfill_eligible.length === 0
                    ? <p className="diagnostic-ok"><Icon name="check" /> {t('maint.backfill.faithful.none')}</p>
                    : <div className="backfill-targets"><strong>{t(pluralKey('maint.backfill.faithful.eligible', status.faithful_backfill_eligible.length), { n: status.faithful_backfill_eligible.length })}</strong><ul>{status.faithful_backfill_eligible.map((name) => <li key={name}>{name}</li>)}</ul></div>}
                  {status.faithful_backfill_missing_originals.length > 0 && <p className="muted">{t(pluralKey('maint.backfill.faithful.missing', status.faithful_backfill_missing_originals.length), { n: status.faithful_backfill_missing_originals.length })}</p>}
                </div>
                <button className="btn accent" onClick={runFaithfulBackfill} disabled={Boolean(busy) || status.faithful_backfill_eligible.length === 0}>
                  {busy === 'faithful' ? t('maint.backfill.faithful.running') : t('maint.backfill.faithful.action', { n: status.faithful_backfill_eligible.length })}
                </button>
              </article>
            </div>
          )}
        </div>
      )}

      {section === 'duplicates' && (
        <div className="maintenance-section" role="tabpanel">
          <DedupPanel viewScope={viewScope} embedded />
        </div>
      )}

      {section === 'trash' && (
        <div className="maintenance-section" role="tabpanel">
          <TrashPanel viewScope={viewScope} scopeLabel={scopeLabel}
                      onChanged={() => { load(); onChanged?.() }} />
        </div>
      )}
    </section>
  )
}
