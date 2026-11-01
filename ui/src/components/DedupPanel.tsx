import { useCallback, useEffect, useState } from 'react'
import type { DupGroup } from '../api'
import { archiveTranscript, getArchivePlan, getDuplicates } from '../api'
import { TranscriptReader } from './TranscriptReader'
import { displayDocumentTitle } from '../lib/meetings'
import { Icon } from './Icon'
import { Badge } from '../design/primitives'
import { useLatestRequest } from '../lib/latestRequest'
import { useLang } from '../lib/langContext'
import { pluralKey } from '../lib/i18n'

export function DedupPanel({ viewScope = '', embedded = false }: { viewScope?: string; embedded?: boolean }) {
  const { t } = useLang()
  const [groups, setGroups] = useState<DupGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [opened, setOpened] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [beginLoad, isCurrentLoad] = useLatestRequest()

  const load = useCallback(() => {
    const request = beginLoad()
    setLoading(true)
    getDuplicates(viewScope)
      .then((value) => { if (isCurrentLoad(request)) setGroups(value) })
      .finally(() => { if (isCurrentLoad(request)) setLoading(false) })
  }, [beginLoad, isCurrentLoad, viewScope])
  useEffect(() => { load() }, [load])

  async function archive(name: string) {
    setBusy(name)
    try {
      const { documents } = await getArchivePlan(name, viewScope)
      const listed = documents
        .map((document) => `• ${document.title} (${document.name})`)
        .join('\n')
      if (!window.confirm(
        t(pluralKey('dedup.archive.confirm', documents.length), { n: documents.length, list: listed }),
      )) return
      await archiveTranscript(name, viewScope)
      load()
    } catch (e) {
      window.alert(t('dedup.archive.failed', { error: String(e) }))
    } finally {
      setBusy(null)
    }
  }

  // `/api/duplicates` ne rend pas le projet de chaque membre : la portée du
  // LISTAGE est la seule connue ici, et elle est juste — c'est avec elle que le
  // backend a filtré la liste, donc le document en fait partie par
  // construction. Sans elle, on retomberait sur la boîte de réception.
  if (opened) return <TranscriptReader name={opened} documentScope={viewScope}
                                       onBack={() => setOpened(null)} backLabel={t('dedup.back')} />

  return (
    <section className={embedded ? 'maintenance-dedup' : 'card'}>
      <div className="row">
        <h2>{t('dedup.title')} {!loading && <span className="muted small">· {t(pluralKey('dedup.groups', groups.length), { n: groups.length })}</span>}</h2>
      </div>

      {loading ? <p className="muted">{t('dedup.analyzing')}</p>
        : groups.length === 0 ? <p className="muted"><Icon name="check" /> {t('dedup.none')}</p>
        : (
        <div className="dup-list">
          {groups.map((g) => (
            <div key={g.keep} className="dup-group">
              <div className="dup-head">
                <strong>{t('dedup.versions', { n: g.count })}</strong>
                <span className="muted small dup-source">{displayDocumentTitle(g.members[0].title, g.members[0].source ?? g.source)}</span>
                <span className="dup-reason">{t('dedup.keep', { reason: g.reason })}</span>
              </div>
              <ul className="lib">
                {g.members.map((m) => (
                  <li key={m.name} className={`lib-item ${m.keep ? 'dup-keep' : ''}`}>
                    <div className="lib-main" onClick={() => setOpened(m.name)} style={{ cursor: 'pointer' }}>
                      <span className="lib-title">
                        {m.keep && <span className="keep-badge">{t('dedup.member.keep')}</span>}{m.name}
                      </span>
                      <span className="muted small">{m.generated_at || '—'}</span>
                    </div>
                    <div className="badges">
                      {m.language && <Badge tone="neutral">{m.language.toUpperCase()}</Badge>}
                      {m.duration && <Badge tone="neutral">{m.duration}</Badge>}
                      {m.diarization && <Badge tone="accent"><Icon name="users" /> {m.n_speakers ?? '?'}</Badge>}
                      {m.profiled && <Badge tone="llm"><Icon name="sparkles" /></Badge>}
                      {!m.keep && (
                        <button className="btn danger" disabled={busy === m.name} onClick={() => archive(m.name)}>
                          {busy === m.name ? '…' : t('common.archive')}
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
