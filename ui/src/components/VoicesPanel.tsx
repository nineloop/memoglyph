import { useCallback, useEffect, useMemo, useState } from 'react'
import type { Voice } from '../api'
import { getVoices, mergeVoices, renameVoice } from '../api'
import { usePartage } from '../lib/partage'
import { displayMeetingTitle } from '../lib/meetings'
import { Icon } from './Icon'
import { Badge } from '../design/primitives'
import { ScopeControl } from './ScopeControl'
import { useLatestRequest } from '../lib/latestRequest'
import { libelleProjet } from '../lib/libelleProjet'
import { useLang } from '../lib/langContext'
import { pluralKey } from '../lib/i18n'
import type { Traduire } from '../lib/i18n'

type Props = { viewScope: string; scopeLabel: string; onChangerPortee?: () => void }

function voiceName(voice: Voice, t: Traduire): string {
  return voice.label_source === 'manual' && voice.label ? voice.label : t('voices.name', { id: voice.id })
}

function VoiceSummary({ voice, compact = false }: { voice: Voice; compact?: boolean }) {
  const { t } = useLang()
  return (
    <div className={`voice-summary ${compact ? 'compact' : ''}`}>
      <strong>{voiceName(voice, t)}</strong>
      <span>{t(pluralKey('voices.occurrences.count', voice.n_members), { n: voice.n_members })}</span>
      {voice.label_source === 'manual'
        ? <span className="voice-trust confirmed"><Icon name="check" /> {t('voices.confirmed')}</span>
        : <span className="voice-trust"><Icon name="alert" /> {t('voices.identityUnconfirmed')}</span>}
    </div>
  )
}

export function VoicesPanel({ viewScope, scopeLabel, onChangerPortee }: Props) {
  const { t } = useLang()
  const [voices, setVoices] = useState<Voice[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [edit, setEdit] = useState<Record<number, string>>({})
  const [saving, setSaving] = useState<number | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [mergeSourceId, setMergeSourceId] = useState<number | null>(null)
  const [mergeTargetId, setMergeTargetId] = useState<number | null>(null)
  const [merging, setMerging] = useState(false)
  const [beginLoad, isCurrentLoad] = useLatestRequest()

  const allProjects = viewScope === 'all'
  // Lecture seule (Q12) : l'invité voit les noms des voix partagées, sans les
  // renommer ni les fusionner.
  const { lectureSeule } = usePartage()

  const load = useCallback(async () => {
    const request = beginLoad()
    setLoading(true); setError('')
    try {
      const value = await getVoices(viewScope)
      if (isCurrentLoad(request)) setVoices(value)
    } catch (cause) {
      if (isCurrentLoad(request)) setError(String(cause))
    } finally {
      if (isCurrentLoad(request)) setLoading(false)
    }
  }, [beginLoad, isCurrentLoad, viewScope])
  useEffect(() => { load() }, [load])

  /**
   * La voix sélectionnée, avec repli sur la première.
   *
   * Le repli n'est pas un confort : après une fusion, la voix source
   * DISPARAÎT du registre. Sans lui, le détail se viderait silencieusement
   * juste après l'action qui vient de réussir.
   */
  const selected = useMemo(
    () => voices.find((voice) => voice.id === selectedId) ?? voices[0] ?? null,
    [selectedId, voices],
  )

  const mergeSource = useMemo(
    () => voices.find((voice) => voice.id === mergeSourceId) ?? null,
    [mergeSourceId, voices],
  )
  const mergeTarget = useMemo(
    () => voices.find((voice) => voice.id === mergeTargetId) ?? null,
    [mergeTargetId, voices],
  )

  async function save(id: number, value?: string) {
    const label = (value ?? edit[id] ?? '').trim()
    setSaving(id); setError('')
    try {
      await renameVoice(id, label || null, viewScope)
      setEdit((current) => ({ ...current, [id]: label }))
      await load()
    } catch (cause) {
      setError(String(cause))
    } finally {
      setSaving(null)
    }
  }

  function openMerge(source: Voice) {
    const firstTarget = voices.find((voice) => voice.id !== source.id)
    setMergeSourceId(source.id)
    setMergeTargetId(firstTarget?.id ?? null)
  }

  function closeMerge() {
    if (merging) return
    setMergeSourceId(null); setMergeTargetId(null)
  }

  async function confirmMerge() {
    if (!mergeSource || !mergeTarget || merging) return
    setMerging(true); setError('')
    try {
      await mergeVoices(mergeSource.id, mergeTarget.id, viewScope)
      closeMerge()
      setMergeSourceId(null); setMergeTargetId(null)
      await load()
    } catch (cause) {
      setError(String(cause))
    } finally {
      setMerging(false)
    }
  }

  return (
    <section className="card voices-workspace">
      <div className="workspace-heading">
        <div>
          <span className="action-scope">{t('voices.registry.eyebrow')}</span>
          <h1>{t('voices.title')}</h1>
          <p>{t('voices.subtitle')}</p>
        </div>
        <button className="btn" onClick={load} disabled={loading}><Icon name="refresh" /> {t('voices.refresh')}</button>
      </div>

      <ScopeControl
        allProjects={allProjects}
        viewScope={viewScope} onChangerPortee={onChangerPortee}
        canToggle={false}
        scopeLabel={scopeLabel}
        onToggle={() => {}}
        detail={t('voices.scope.detail')}
      />

      <div className="voice-principle">
        <Icon name="sparkles" />
        <div>
          <strong>{t('voices.principle.title')}</strong>
          <p>{t('voices.principle.detail')}</p>
        </div>
      </div>

      {error && <p className="error"><Icon name="error" /> {error}</p>}
      {loading ? (
        <p className="muted">{t('voices.loading')}</p>
      ) : voices.length === 0 ? (
        <div className="empty-state">
          <span className="empty-icon"><Icon name="volume" /></span>
          <strong>{t('voices.empty.title')}</strong>
          <p className="muted">{t('voices.empty.detail')}</p>
        </div>
      ) : (
        <div className="voices-master-detail">
          <ul className="voice-list voice-registry" aria-label={t('voices.registry.aria')}>
            {voices.map((voice) => {
              const confirmed = voice.label_source === 'manual' && Boolean(voice.label)
              const actif = selected?.id === voice.id
              return (
                <li key={voice.id} className="voice-item">
                  {/* Un `<h2>` ne peut pas vivre dans un `<button>` : le titre
                      de la voix est dans le détail, la ligne porte son nom. */}
                  <button
                    type="button"
                    className={actif ? 'voice-row on' : 'voice-row'}
                    aria-current={actif ? 'true' : undefined}
                    onClick={() => setSelectedId(voice.id)}
                  >
                    <span className="voice-row-nom">
                      {confirmed ? voice.label : t('voices.name', { id: voice.id })}
                      <small>
                        {confirmed
                          ? t('voices.confirmed')
                          : voice.suggestion ? t('voices.suggestion', { suggestion: voice.suggestion }) : t('voices.unconfirmed')}
                      </small>
                    </span>
                    <span className="voice-row-compte" title={t(pluralKey('voices.occurrences.count', voice.n_members), { n: voice.n_members })}>
                      {voice.n_members}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>

          {selected && (() => {
            const voice = selected
            const confirmed = voice.label_source === 'manual' && Boolean(voice.label)
            const legacyAutomatic = voice.label_source === 'auto' && Boolean(voice.label)
            return (
              <div className="voice-card voice-detail">
                <div className="voice-card-head">
                  <div>
                    <span className="voice-id">{t('voices.fingerprint', { id: voice.id })}</span>
                    <h2>{confirmed ? voice.label : t('voices.name', { id: voice.id })}</h2>
                  </div>
                  <div className="badges">
                    {confirmed
                      ? <Badge tone="warn"><Icon name="check" /> {t('voices.confirmed')}</Badge>
                      : <Badge tone="neutral"><Icon name="alert" /> {t('voices.unconfirmed')}</Badge>}
                    <Badge tone="llm">{t(pluralKey('voices.occurrences.count', voice.n_members), { n: voice.n_members })}</Badge>
                  </div>
                </div>

                <div className="voice-provenance">
                  <span className="muted small">{t('voices.provenance')}</span>
                  <strong>{confirmed ? t('voices.provenance.manual') : legacyAutomatic ? t('voices.provenance.legacy') : t('voices.provenance.none')}</strong>
                </div>

                {!lectureSeule && !confirmed && voice.suggestion && (
                  <div className="voice-suggestion">
                    <div>
                      <span><Icon name="sparkles" /> {t('voices.suggestion.llm')}</span>
                      <strong>{voice.suggestion}</strong>
                      <small>{t('voices.suggestion.hint')}</small>
                    </div>
                    <button className="btn accent" onClick={() => save(voice.id, voice.suggestion!)} disabled={saving === voice.id}>
                      {t('voices.suggestion.confirm', { suggestion: voice.suggestion })}
                    </button>
                  </div>
                )}

                {!lectureSeule && <div className="voice-name-editor">
                  <label htmlFor={`voice-name-${voice.id}`}>{confirmed ? t('voices.editor.modify') : t('voices.editor.name')}</label>
                  <div>
                    <input
                      id={`voice-name-${voice.id}`}
                      className="voice-name"
                      placeholder={t('voices.editor.placeholder', { id: voice.id })}
                      value={edit[voice.id] ?? voice.label ?? ''}
                      onChange={(event) => setEdit((current) => ({ ...current, [voice.id]: event.target.value }))}
                      onKeyDown={(event) => { if (event.key === 'Enter') save(voice.id) }}
                    />
                    <button className="btn" onClick={() => save(voice.id)} disabled={saving === voice.id}>
                      {saving === voice.id ? t('voices.saving') : confirmed ? t('voices.update') : t('voices.saveConfirmed')}
                    </button>
                  </div>
                </div>}

                <div className="voice-occurrences">
                  <div className="voice-section-title"><strong>{t('voices.occurrences.title')}</strong><span>{voice.occurrences.length}</span></div>
                  <ul>
                    {voice.occurrences.map((occurrence) => (
                      <li key={`${occurrence.document_id ?? occurrence.name}:${occurrence.speaker_local}`}>
                        <div>
                          <strong>{displayMeetingTitle(occurrence.title || occurrence.name, occurrence.source)}</strong>
                          <span>{occurrence.speaker_local} · {occurrence.name}</span>
                        </div>
                        <Badge tone="accent"><Icon name="folder" /> {occurrence.project ? libelleProjet(occurrence.project, t) : t('voices.occurrence.unknownProject')}</Badge>
                      </li>
                    ))}
                  </ul>
                </div>

                {!lectureSeule && voices.length > 1 && (
                  <button className="btn voice-compare" onClick={() => openMerge(voice)}>
                    <Icon name="users" /> {t('voices.compareMerge')}
                  </button>
                )}
              </div>
            )
          })()}
        </div>
      )}

      {mergeSource && (
        <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) closeMerge() }}>
          <section className="merge-dialog" role="dialog" aria-modal="true" aria-labelledby="merge-title">
            <div className="merge-dialog-head">
              <div><span className="action-scope">{t('voices.merge.eyebrow')}</span><h2 id="merge-title">{t('voices.merge.title')}</h2></div>
              <button className="icon-btn" aria-label={t('voices.merge.close')} onClick={closeMerge} disabled={merging}>×</button>
            </div>

            <label className="merge-target-label" htmlFor="merge-target">{t('voices.merge.into')}</label>
            <select id="merge-target" className="project-select" value={mergeTargetId ?? ''}
                    onChange={(event) => setMergeTargetId(Number(event.target.value))}>
              {voices.filter((voice) => voice.id !== mergeSource.id).map((voice) => (
                <option key={voice.id} value={voice.id}>{voiceName(voice, t)} · {t(pluralKey('voices.occurrences.count', voice.n_members), { n: voice.n_members })}</option>
              ))}
            </select>

            <div className="merge-comparison">
              <div className="merge-source"><span className="action-scope">{t('voices.merge.willDisappear')}</span><VoiceSummary voice={mergeSource} /></div>
              <span className="merge-arrow">→</span>
              <div className="merge-destination"><span className="action-scope">{t('voices.merge.willKeep')}</span>{mergeTarget && <VoiceSummary voice={mergeTarget} />}</div>
            </div>

            <p className="warn small"><Icon name="alert" /> {t('voices.merge.warn')}</p>
            <div className="dialog-actions">
              <button className="btn" onClick={closeMerge} disabled={merging}>{t('common.cancel')}</button>
              <button className="btn danger" onClick={confirmMerge} disabled={!mergeTarget || merging}>
                {merging ? t('voices.merge.merging') : t('voices.merge.confirm')}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  )
}
