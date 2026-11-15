import { useCallback, useEffect, useState } from 'react'
import type { DupGroup } from '../api'
import { archiveTranscript, getArchivePlan, getDuplicates } from '../api'
import { TranscriptReader } from './TranscriptReader'
import { displayDocumentTitle } from '../lib/meetings'
import { Icon } from './Icon'
import { Badge, EtatEchec } from '../design/primitives'
import { useLatestRequest } from '../lib/latestRequest'
import { useLang } from '../lib/langContext'
import { useConfirmer } from '../design/Confirmation'
import { phraseDErreur } from '../lib/erreurs'
import { pluralKey } from '../lib/i18n'
import type { MessageKey } from '../lib/messages'

/** Les raisons que le serveur écrit en français (`core/dedup.py`), associées à leur phrase dans la langue de l'interface. */
const RAISONS_DU_SERVEUR: Record<string, MessageKey> = {
  'le plus récent': 'dedup.reason.recent',
  'le plus complet — diarisé': 'dedup.reason.diarized',
  'le plus complet — profilé': 'dedup.reason.profiled',
  'le plus complet — diarisé + profilé': 'dedup.reason.both',
}

export function DedupPanel({ viewScope = '', embedded = false }: { viewScope?: string; embedded?: boolean }) {
  const { t, lang } = useLang()
  const confirmer = useConfirmer()
  const [groups, setGroups] = useState<DupGroup[]>([])
  // Trois états, jamais deux : « vide » ne s'affiche que quand le chargement a ABOUTI.
  const [etat, setEtat] = useState<'chargement' | 'echec' | 'ok'>('chargement')
  // L'échec qui SUIT le « Réessayer » de la personne reçoit le focus (le bouton se remonte) ; un autre ne le prend pas.
  const [apresReprise, setApresReprise] = useState(false)
  const [opened, setOpened] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [erreur, setErreur] = useState('')
  const [beginLoad, isCurrentLoad] = useLatestRequest()

  const load = useCallback((reprise = false) => {
    const request = beginLoad()
    setEtat('chargement')
    setApresReprise(reprise)
    getDuplicates(viewScope)
      .then((value) => { if (isCurrentLoad(request)) { setGroups(value); setEtat('ok') } })
      .catch(() => { if (isCurrentLoad(request)) setEtat('echec') })
  }, [beginLoad, isCurrentLoad, viewScope])
  useEffect(() => { load() }, [load])

  async function archive(name: string) {
    setBusy(name)
    setErreur('')
    try {
      const { documents } = await getArchivePlan(name, viewScope)
      const accepte = await confirmer({
        titre: t(pluralKey('dedup.archive.titre', documents.length), { n: documents.length }),
        phrase: t(pluralKey('dedup.archive.confirm', documents.length)),
        details: documents.map((document) => `${document.title} (${document.name})`),
        libelleAction: t('common.archive'),
      })
      if (!accepte) return
      await archiveTranscript(name, viewScope)
      load()
    } catch (e) {
      // L'échec se dit sur l'écran, par la phrase du catalogue ou celle du contexte : jamais une boîte native ni le texte brut.
      setErreur(phraseDErreur(t, lang, e, t('dedup.archive.failed')))
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
        <h2>{t('dedup.title')} {etat === 'ok' && <span className="muted small">· {t(pluralKey('dedup.groups', groups.length), { n: groups.length })}</span>}</h2>
      </div>

      {erreur && <p className="error" role="alert"><Icon name="error" /> {erreur}</p>}

      {etat === 'chargement' ? <p className="muted">{t('dedup.analyzing')}</p>
        : etat === 'echec' ? <EtatEchec onRetry={() => load(true)} focusRetry={apresReprise}>{t('dedup.loadFailed')}</EtatEchec>
        : groups.length === 0 ? <p className="muted"><Icon name="check" /> {t('dedup.none')}</p>
        : (
        <div className="dup-list">
          {groups.map((g) => (
            <div key={g.keep} className="dup-group">
              <div className="dup-head">
                <strong>{t('dedup.versions', { n: g.count })}</strong>
                <span className="muted small dup-source">{displayDocumentTitle(g.members[0].title, g.members[0].source ?? g.source)}</span>
                <span className="dup-reason">{t('dedup.keep', { reason: RAISONS_DU_SERVEUR[g.reason] ? t(RAISONS_DU_SERVEUR[g.reason]) : g.reason })}</span>
              </div>
              <ul className="lib">
                {g.members.map((m) => (
                  <li key={m.name} className={`lib-item ${m.keep ? 'dup-keep' : ''}`}>
                    <button type="button" className="lib-main" onClick={() => setOpened(m.name)}>
                      <span className="lib-title">
                        {m.keep && <span className="keep-badge">{t('dedup.member.keep')}</span>}{m.name}
                      </span>
                      <span className="muted small">{m.generated_at || '—'}</span>
                    </button>
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
