import { useEffect, useMemo, useState } from 'react'
import {
  getUsageRollup, getUsageExecutions, getUsageExecutionDetail,
  type UsageRollup, type UsageRollupRow, type UsageExecution, type FinopsCall,
} from '../api'
import { useLang } from '../lib/langContext'
import { formatCost, formatTokens, formatTime, formatAudio } from '../lib/finopsFormat'

// Tableau de bord Usage (phase 3B) — compact, orienté décision, lecture seule, BORNÉ PROJET.
// Règles dures respectées partout : devises jamais mélangées, cloud_derived vs local_imputed
// jamais additionnés, non tarifé (NULL) visible mais jamais un faux zéro, local marqué « estimé ».

interface Props {
  viewScope: string        // un projet, ou 'all' pour la vue globale (opt-in explicite)
  scopeLabel: string
}

type Load = 'loading' | 'error' | 'ready'

/** Total par (base, devise) — on n'additionne JAMAIS deux devises ni deux bases. */
interface BasisTotal {
  cost_basis: string | null
  currency: string | null
  cost: number | null
  n_executions: number
  n_model_calls: number
}

function foldByBasis(rows: UsageRollupRow[]): BasisTotal[] {
  const map = new Map<string, BasisTotal>()
  for (const r of rows) {
    const key = `${r.cost_basis ?? '∅'}|${r.currency ?? '∅'}`
    const cur = map.get(key)
    if (cur) {
      cur.cost = r.cost === null && cur.cost === null ? null : (cur.cost ?? 0) + (r.cost ?? 0)
      cur.n_executions += r.n_executions
      cur.n_model_calls += r.n_model_calls
    } else {
      map.set(key, {
        cost_basis: r.cost_basis, currency: r.currency, cost: r.cost,
        n_executions: r.n_executions, n_model_calls: r.n_model_calls,
      })
    }
  }
  return [...map.values()]
}

function sum(rows: UsageRollupRow[], f: (r: UsageRollupRow) => number): number {
  return rows.reduce((a, r) => a + f(r), 0)
}

const BASIS_CLASS: Record<string, string> = {
  cloud_derived: 'usage-cloud', local_imputed: 'usage-local', mixed: 'usage-mixed',
}
const basisClass = (b: string | null) => (b && BASIS_CLASS[b]) || 'usage-unpriced'

/** Barres horizontales : une par seau, longueur ∝ coût (ou compte), couleur = base. */
function HBars({ rows, valueOf, labelOf, onPick, t }: {
  rows: UsageRollupRow[]
  valueOf: (r: UsageRollupRow) => number
  labelOf: (r: UsageRollupRow) => string
  onPick?: (r: UsageRollupRow) => void
  t: ReturnType<typeof useLang>['t']
}) {
  const max = Math.max(1, ...rows.map(valueOf))
  return (
    <ul className="usage-bars">
      {rows.map((r, i) => {
        const v = valueOf(r)
        const pct = Math.round((v / max) * 100)
        return (
          <li key={i}>
            <button type="button" className="usage-bar-row"
              disabled={!onPick} onClick={() => onPick?.(r)}>
              <span className="usage-bar-label">{labelOf(r)}</span>
              <span className="usage-bar-track">
                <span className={`usage-bar-fill ${basisClass(r.cost_basis)}`}
                  style={{ width: `${pct}%` }} />
              </span>
              <span className="usage-bar-value">{formatCost(r, t)}</span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

/** Barres par jour, GROUPÉES par base (jamais empilées → aucune somme trompeuse). */
function DayBars({ rows, onPick, t }: {
  rows: UsageRollupRow[]
  onPick?: (r: UsageRollupRow) => void
  t: ReturnType<typeof useLang>['t']
}) {
  const priced = rows.filter((r) => r.cost !== null)
  const max = Math.max(1, ...priced.map((r) => r.cost as number))
  const days = [...new Set(rows.map((r) => r.bucket))].sort()
  return (
    <div className="usage-days">
      {days.map((day) => {
        const dayRows = rows.filter((r) => r.bucket === day)
        return (
          <div key={day ?? '∅'} className="usage-day">
            <span className="usage-day-bars">
              {dayRows.map((r, i) => (
                <button key={i} type="button"
                  className={`usage-day-bar ${basisClass(r.cost_basis)}`}
                  title={`${day} · ${formatCost(r, t)}`}
                  disabled={!onPick} onClick={() => onPick?.(r)}
                  style={{ height: `${r.cost === null ? 4 : Math.max(3, Math.round(((r.cost as number) / max) * 100))}%` }} />
              ))}
            </span>
            <span className="usage-day-label">{(day ?? '').slice(5)}</span>
          </div>
        )
      })}
    </div>
  )
}

interface Drill { dim: 'provider' | 'model' | 'stage' | 'day'; value: string; label: string }

export function UsagePanel({ viewScope, scopeLabel }: Props) {
  const { t } = useLang()
  const [state, setState] = useState<Load>('loading')
  const [byBasis, setByBasis] = useState<UsageRollup | null>(null)
  const [byDay, setByDay] = useState<UsageRollup | null>(null)
  const [byProvider, setByProvider] = useState<UsageRollup | null>(null)
  const [byStage, setByStage] = useState<UsageRollup | null>(null)
  const [drill, setDrill] = useState<Drill | null>(null)

  useEffect(() => {
    let alive = true
    setState('loading')
    setDrill(null)
    Promise.all([
      getUsageRollup(viewScope, 'cost_basis'),
      getUsageRollup(viewScope, 'day'),
      getUsageRollup(viewScope, 'provider'),
      getUsageRollup(viewScope, 'stage'),
    ]).then(([b, d, p, s]) => {
      if (!alive) return
      setByBasis(b); setByDay(d); setByProvider(p); setByStage(s)
      setState('ready')
    }).catch(() => { if (alive) setState('error') })
    return () => { alive = false }
  }, [viewScope])

  const totals = useMemo(() => foldByBasis(byBasis?.rows ?? []), [byBasis])
  const cloud = totals.filter((r) => r.cost_basis === 'cloud_derived')
  const local = totals.filter((r) => r.cost_basis === 'local_imputed')
  const unpriced = totals.filter((r) => r.cost_basis === null || r.cost_basis === 'mixed')
  const allRows = byBasis?.rows ?? []
  const nCalls = sum(allRows, (r) => r.n_model_calls)
  const nExec = sum(allRows, (r) => r.n_executions)
  const tokens = sum(allRows, (r) => r.in_tok + r.out_tok)
  const audio = sum(allRows, (r) => r.audio_seconds)
  const wall = sum(allRows, (r) => r.wall_clock_s)
  const isEmpty = state === 'ready' && allRows.length === 0

  if (state === 'loading') return <div className="usage-panel"><p className="muted">{t('usage.loading')}</p></div>
  if (state === 'error') return <div className="usage-panel"><p className="muted">{t('usage.unavailable')}</p></div>

  return (
    <div className="usage-panel">
      <header className="usage-head">
        <h2>{t('usage.title')}</h2>
        <span className="usage-scope">{viewScope === 'all' ? t('usage.scope.global') : scopeLabel}</span>
      </header>

      {isEmpty && <p className="muted usage-empty">{t('usage.empty')}</p>}

      {!isEmpty && (
        <>
          <section className="usage-kpis">
            <KpiCost title={t('usage.kpi.cloud')} totals={cloud} t={t} klass="usage-cloud" />
            <KpiCost title={t('usage.kpi.local')} totals={local} t={t} klass="usage-local" />
            <div className="usage-kpi usage-unpriced-kpi">
              <span className="usage-kpi-title">{t('usage.kpi.unpriced')}</span>
              <span className="usage-kpi-main">
                {unpriced.length === 0 ? '0'
                  : `${unpriced.reduce((a, r) => a + r.n_model_calls + r.n_executions, 0)}`}
                {unpriced.length > 0 && <span className="usage-unpriced-dot" aria-hidden />}
              </span>
              <span className="usage-kpi-sub">{t('finops.unpriced')}</span>
            </div>
          </section>

          <section className="usage-stats">
            <Stat label={t('usage.kpi.executions')} value={String(nExec)} />
            <Stat label={t('usage.kpi.calls')} value={String(nCalls)} />
            <Stat label={t('usage.kpi.tokens')} value={formatTokens(tokens)} />
            <Stat label={t('usage.kpi.audio')} value={formatAudio(audio)} />
            <Stat label={t('usage.kpi.time')} value={formatTime(wall)} />
          </section>

          <section className="usage-charts">
            <div className="usage-chart">
              <h3>{t('usage.chart.overTime')}</h3>
              <DayBars rows={byDay?.rows ?? []} t={t}
                onPick={(r) => r.bucket && setDrill({ dim: 'day', value: r.bucket, label: r.bucket })} />
            </div>
            <div className="usage-chart">
              <h3>{t('usage.chart.providerMix')}</h3>
              <HBars rows={(byProvider?.rows ?? []).filter((r) => r.bucket)}
                valueOf={(r) => r.cost ?? 0} labelOf={(r) => r.bucket ?? '—'} t={t}
                onPick={(r) => r.bucket && setDrill({ dim: 'provider', value: r.bucket, label: r.bucket })} />
            </div>
            <div className="usage-chart">
              <h3>{t('usage.chart.byStage')}</h3>
              <HBars rows={(byStage?.rows ?? []).filter((r) => r.bucket && r.bucket !== '(execution)')}
                valueOf={(r) => r.cost ?? 0} labelOf={(r) => r.bucket ?? '—'} t={t}
                onPick={(r) => r.bucket && setDrill({ dim: 'stage', value: r.bucket, label: r.bucket })} />
            </div>
          </section>

          <UsageLegend t={t} />

          {drill && <DrillDown viewScope={viewScope} drill={drill} onClose={() => setDrill(null)} t={t} />}
        </>
      )}
    </div>
  )
}

function KpiCost({ title, totals, t, klass }: {
  title: string; totals: BasisTotal[]; t: ReturnType<typeof useLang>['t']; klass: string
}) {
  return (
    <div className={`usage-kpi ${klass}`}>
      <span className="usage-kpi-title">{title}</span>
      {totals.length === 0
        ? <span className="usage-kpi-main">—</span>
        : totals.map((r, i) => (
          <span key={i} className="usage-kpi-main">{formatCost(r, t)}</span>
        ))}
      <span className="usage-kpi-sub">
        {totals.reduce((a, r) => a + r.n_executions, 0)} · {totals.reduce((a, r) => a + r.n_model_calls, 0)}
      </span>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="usage-stat"><span className="usage-stat-value">{value}</span><span className="usage-stat-label">{label}</span></div>
}

function UsageLegend({ t }: { t: ReturnType<typeof useLang>['t'] }) {
  return (
    <ul className="usage-legend">
      <li><span className="usage-swatch usage-cloud" />{t('usage.legend.cloud')}</li>
      <li><span className="usage-swatch usage-local" />{t('usage.legend.local')}</li>
      <li><span className="usage-swatch usage-unpriced" />{t('usage.legend.unpriced')}</li>
    </ul>
  )
}

/** Liste d'exécutions BORNÉE (paginée) pour un segment sélectionné → détail model_call. */
function DrillDown({ viewScope, drill, onClose, t }: {
  viewScope: string; drill: Drill; onClose: () => void; t: ReturnType<typeof useLang>['t']
}) {
  const [execs, setExecs] = useState<UsageExecution[] | 'loading' | 'error'>('loading')
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const PAGE = 10

  const filters = useMemo(() => {
    if (drill.dim === 'provider') return { provider: drill.value }
    if (drill.dim === 'model') return { model: drill.value }
    if (drill.dim === 'stage') return { stage: drill.value }
    return { start: drill.value, end: drill.value }   // day
  }, [drill])

  useEffect(() => {
    let alive = true
    setExecs('loading'); setOffset(0)
    getUsageExecutions(viewScope, filters, PAGE, 0)
      .then((p) => { if (alive) { setExecs(p.executions); setHasMore(p.has_more) } })
      .catch(() => { if (alive) setExecs('error') })
    return () => { alive = false }
  }, [viewScope, filters])

  const loadMore = () => {
    const next = offset + PAGE
    getUsageExecutions(viewScope, filters, PAGE, next).then((p) => {
      setExecs((cur) => (Array.isArray(cur) ? [...cur, ...p.executions] : p.executions))
      setOffset(next); setHasMore(p.has_more)
    }).catch(() => { /* best-effort */ })
  }

  return (
    <section className="usage-drill">
      <header className="usage-drill-head">
        <h3>{t('usage.drill.title')} · <span className="usage-drill-seg">{drill.label}</span></h3>
        <button type="button" className="usage-drill-close" onClick={onClose}>{t('usage.drill.close')}</button>
      </header>
      {execs === 'loading' && <p className="muted small">{t('usage.loading')}</p>}
      {execs === 'error' && <p className="muted small">{t('usage.unavailable')}</p>}
      {Array.isArray(execs) && execs.length === 0 && <p className="muted small">{t('usage.drill.none')}</p>}
      {Array.isArray(execs) && execs.length > 0 && (
        <ul className="usage-exec-list">
          {execs.map((e) => <ExecRow key={e.execution_id} viewScope={viewScope} exec={e} t={t} />)}
        </ul>
      )}
      {Array.isArray(execs) && hasMore && (
        <button type="button" className="usage-more" onClick={loadMore}>{t('usage.drill.loadMore')}</button>
      )}
    </section>
  )
}

function ExecRow({ viewScope, exec, t }: {
  viewScope: string; exec: UsageExecution; t: ReturnType<typeof useLang>['t']
}) {
  const [calls, setCalls] = useState<FinopsCall[] | 'loading' | 'error' | null>(null)
  const onToggle = (open: boolean) => {
    if (!open || calls !== null) return
    setCalls('loading')
    getUsageExecutionDetail(viewScope, exec.execution_id)
      .then((d) => setCalls(d.available && d.calls ? d.calls : 'error'))
      .catch(() => setCalls('error'))
  }
  const isStt = exec.kind === 'stt'
  return (
    <li className="usage-exec">
      <details onToggle={(ev) => onToggle((ev.target as HTMLDetailsElement).open)}>
        <summary>
          <code className="finops-model">{exec.model_primary || exec.provider_primary || '—'}</code>
          <span className="usage-exec-meta">
            {(exec.started_at || '').replace('T', ' ').slice(0, 16)} · {exec.kind}
            {isStt ? ` · ${formatAudio(exec.audio_seconds)}` : ` · ${exec.n_calls ?? 0} ${t('usage.kpi.calls')}`}
          </span>
          <span className="usage-exec-cost">{formatCost(exec as never, t)}</span>
        </summary>
        {calls === 'loading' && <p className="muted small">{t('usage.loading')}</p>}
        {calls === 'error' && <p className="muted small">{t('usage.unavailable')}</p>}
        {Array.isArray(calls) && calls.length === 0 && <p className="muted small">{t('finops.noCalls')}</p>}
        {Array.isArray(calls) && calls.length > 0 && (
          <div className="finops-table-scroll">
            <table className="finops-table">
              <thead>
                <tr>
                  <th>{t('finops.detail.stage')}</th><th>{t('finops.detail.model')}</th>
                  <th>{t('finops.detail.tokens')}</th><th>{t('finops.detail.latency')}</th>
                  <th>{t('finops.detail.status')}</th><th>{t('finops.detail.cost')}</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((c) => (
                  <tr key={c.seq}>
                    <td>{c.stage || '—'}</td>
                    <td><code className="finops-model">{c.model || c.provider || '—'}</code></td>
                    <td className="finops-num">
                      {c.in_tok === null && c.out_tok === null ? '—'
                        : `${formatTokens(c.in_tok)}/${formatTokens(c.out_tok)}${c.cached_tok ? ` (${formatTokens(c.cached_tok)} ${t('finops.detail.cached')})` : ''}`}
                    </td>
                    <td className="finops-num">{c.latency_s === null ? '—' : `${c.latency_s.toFixed(1)}s`}</td>
                    <td>{c.status || '—'}</td>
                    <td className="finops-num">{formatCost({ cost: c.cost, currency: c.cost_currency, cost_basis: c.cost_basis }, t)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </li>
  )
}
