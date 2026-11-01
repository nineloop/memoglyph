import { useEffect, useState } from 'react'
import {
  getFinopsSummary, getFinopsExecution,
  type FinopsSummary, type FinopsCall,
} from '../api'
import { useLang } from '../lib/langContext'
import {
  formatCost, formatTokens, formatTime, formatAudio,
} from '../lib/finopsFormat'

type State = 'loading' | 'error' | FinopsSummary

/**
 * Section compacte « usage & coût IA » pour UN document (transcript ou CR), lecture seule.
 * Best-effort : une lecture en échec n'affecte jamais le reste de la vue (état « indisponible »).
 * Ne combine JAMAIS deux devises ; noms provider/model VERBATIM ; NULL => « — unpriced ».
 */
export function FinopsSection({ name, viewScope, label }: { name: string; viewScope: string; label: string }) {
  const { t } = useLang()
  const [state, setState] = useState<State>('loading')

  useEffect(() => {
    let alive = true
    setState('loading')
    getFinopsSummary(name, viewScope)
      .then((d) => { if (alive) setState(d) })
      .catch(() => { if (alive) setState('error') })
    return () => { alive = false }
  }, [name, viewScope])

  if (state === 'loading') return null
  if (state === 'error') {
    return (
      <div className="finops-block">
        <div className="action-scope">{label}</div>
        <p className="muted small">{t('finops.unavailable')}</p>
      </div>
    )
  }
  // Un transcript porte aussi son profilage (exécution `profile`), au même contrat.
  const profil = state.profile?.available ? state.profile : null
  if (!state.available && !profil) return null   // aucune exécution liée : on n'invente rien

  return (
    <>
      {state.available && (
        <ExecutionBlock name={name} viewScope={viewScope} label={label} summary={state} />
      )}
      {profil && (
        <ExecutionBlock name={name} viewScope={viewScope} label={t('finops.profile')} summary={profil} />
      )}
    </>
  )
}

/** Une exécution : fournisseur, modèle, coût, puis audio (STT) ou appels et jetons. */
function ExecutionBlock({ name, viewScope, label, summary: s }: {
  name: string; viewScope: string; label: string; summary: FinopsSummary
}) {
  const { t } = useLang()
  const isStt = s.kind === 'stt'
  return (
    <div className="finops-block">
      <div className="action-scope">{label}</div>
      <dl className="finops-grid">
        <div><dt>{t('finops.provider')}</dt><dd>{s.provider || '—'}</dd></div>
        <div><dt>{t('finops.model')}</dt><dd><code className="finops-model">{s.model || '—'}</code></dd></div>
        <div><dt>{t('finops.cost')}</dt><dd className="finops-cost">{formatCost(s, t)}</dd></div>
        {isStt
          ? <div><dt>{t('finops.audio')}</dt><dd>{formatAudio(s.audio_seconds)}</dd></div>
          : <div><dt>{t('finops.calls')}</dt><dd>{s.calls ?? '—'}</dd></div>}
        {!isStt && <div><dt>{t('finops.tokens')}</dt><dd>{formatTokens(s.tokens)}</dd></div>}
        <div><dt>{t('finops.time')}</dt><dd>{formatTime(s.wall_clock_s)}</dd></div>
      </dl>
      {s.execution_id && (
        <ExecutionDetails name={name} viewScope={viewScope} executionId={s.execution_id} />
      )}
    </div>
  )
}

/** Divulgation progressive (natif `<details>`) : charge le forensique À L'OUVERTURE seulement. */
function ExecutionDetails({ name, viewScope, executionId }: { name: string; viewScope: string; executionId: string }) {
  const { t } = useLang()
  const [calls, setCalls] = useState<FinopsCall[] | null | 'error'>(null)
  const [loaded, setLoaded] = useState(false)

  const onToggle = (e: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (!e.currentTarget.open || loaded) return
    setLoaded(true)
    getFinopsExecution(name, executionId, viewScope)
      .then((d) => setCalls(d.available && d.calls ? d.calls : []))
      .catch(() => setCalls('error'))
  }

  return (
    <details className="finops-details" onToggle={onToggle}>
      <summary>{t('finops.viewDetails')}</summary>
      {calls === null && <p className="muted small">{t('finops.loading')}</p>}
      {calls === 'error' && <p className="muted small">{t('finops.unavailable')}</p>}
      {Array.isArray(calls) && calls.length === 0 && <p className="muted small">{t('finops.noCalls')}</p>}
      {Array.isArray(calls) && calls.length > 0 && (
        <div className="finops-table-scroll">
          <table className="finops-table">
            <thead>
              <tr>
                <th>{t('finops.detail.stage')}</th>
                <th>{t('finops.detail.model')}</th>
                <th>{t('finops.detail.tokens')}</th>
                <th>{t('finops.detail.latency')}</th>
                <th>{t('finops.detail.status')}</th>
                <th>{t('finops.detail.cost')}</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.seq}>
                  <td>{c.stage || '—'}</td>
                  <td><code className="finops-model">{c.model || c.provider || '—'}</code></td>
                  <td className="finops-num">
                    {c.in_tok === null && c.out_tok === null
                      ? '—'
                      : `${formatTokens(c.in_tok)}/${formatTokens(c.out_tok)}${c.cached_tok ? ` (${formatTokens(c.cached_tok)} ${t('finops.detail.cached')})` : ''}`}
                  </td>
                  <td className="finops-num">{c.latency_s === null ? '—' : `${c.latency_s.toFixed(1)}s`}</td>
                  <td>{c.status || '—'}</td>
                  <td className="finops-num">{formatCost(c, t)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  )
}
