import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { UsagePanel } from '../components/UsagePanel'
import { LangProvider } from '../components/LangProvider'
import {
  getUsageRollup, getUsageExecutions, getUsageExecutionDetail,
  type UsageGroupBy, type UsageRollupRow,
} from '../api'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    getUsageRollup: vi.fn(), getUsageExecutions: vi.fn(), getUsageExecutionDetail: vi.fn(),
  }
})

const row = (o: Partial<UsageRollupRow> & { bucket: string | null }): UsageRollupRow => ({
  group_by: 'x', currency: 'USD', cost_basis: 'cloud_derived',
  n_executions: 0, n_model_calls: 0, in_tok: 0, out_tok: 0, cached_tok: 0,
  reasoning_tok: 0, audio_seconds: 0, wall_clock_s: 0, cost: 0, ...o,
})

/** Jeu de données : nuage dérivé + local estimé + non tarifé, deux fournisseurs, deux étapes. */
function mockRollups() {
  vi.mocked(getUsageRollup).mockImplementation((_scope: string, groupBy: UsageGroupBy) => {
    if (groupBy === 'cost_basis') return Promise.resolve({ group_by: 'cost_basis', scope: 'all', rows: [
      row({ bucket: 'cloud_derived', cost_basis: 'cloud_derived', currency: 'USD', cost: 0.0131, n_executions: 2, n_model_calls: 4, in_tok: 5000, out_tok: 1200 }),
      row({ bucket: 'local_imputed', cost_basis: 'local_imputed', currency: 'USD', cost: 0.0075, n_executions: 1, n_model_calls: 0, audio_seconds: 300, wall_clock_s: 12 }),
      row({ bucket: null, cost_basis: null, currency: null, cost: null, n_executions: 1, n_model_calls: 1, in_tok: 800, out_tok: 200 }),
    ] })
    if (groupBy === 'day') return Promise.resolve({ group_by: 'day', scope: 'all', rows: [
      row({ bucket: '2026-08-09', cost_basis: 'cloud_derived', cost: 0.010 }),
      row({ bucket: '2026-08-10', cost_basis: 'local_imputed', cost: 0.0075 }),
    ] })
    if (groupBy === 'provider') return Promise.resolve({ group_by: 'provider', scope: 'all', rows: [
      row({ bucket: 'openai', cost_basis: 'cloud_derived', cost: 0.0131 }),
      row({ bucket: 'whisper', cost_basis: 'local_imputed', cost: 0.0075 }),
    ] })
    return Promise.resolve({ group_by: 'stage', scope: 'all', rows: [
      row({ bucket: 'extract', cost_basis: 'cloud_derived', cost: 0.0131 }),
      row({ bucket: 'stt', cost_basis: 'local_imputed', cost: 0.0075 }),
      row({ bucket: '(execution)', cost_basis: 'cloud_derived', cost: null, n_executions: 2 }),
    ] })
  })
}

const renderPanel = (scope = 'all', lang: 'fr' | 'en' = 'fr') =>
  render(<LangProvider initiale={lang}><UsagePanel viewScope={scope} scopeLabel="Projet X" /></LangProvider>)

beforeEach(() => {
  vi.mocked(getUsageExecutions).mockResolvedValue({ executions: [], limit: 10, offset: 0, has_more: false, scope: 'all' })
  vi.mocked(getUsageExecutionDetail).mockResolvedValue({ available: true, calls: [] })
})

describe('UsagePanel — KPIs (jamais mélangés)', () => {
  it('affiche nuage DÉRIVÉ et local ESTIMÉ séparément, non tarifé visible', async () => {
    mockRollups()
    renderPanel()
    // Nuage dérivé : $0.0131 dérivé (apparaît en KPI ET dans les graphiques)
    expect((await screen.findAllByText('$0.0131 dérivé')).length).toBeGreaterThan(0)
    // Local estimé : préfixe ~ et étiquette estimé (coût < 0.01 => 5 décimales)
    expect(screen.getAllByText('~$0.00750 estimé').length).toBeGreaterThan(0)
    // Non tarifé visible, jamais un faux zéro
    expect(screen.getAllByText('— non tarifé').length).toBeGreaterThan(0)
    expect(getUsageRollup).toHaveBeenCalledWith('all', 'cost_basis')
  })

  it('ne fabrique aucun total global mélangeant nuage et local', async () => {
    mockRollups()
    renderPanel()
    await screen.findAllByText('$0.0131 dérivé')
    // 0.0131 + 0.0075 = 0.0206 ne doit APPARAÎTRE nulle part (pas de grand total trompeur).
    expect(screen.queryByText(/0\.0206/)).toBeNull()
  })

  it('rend le local ESTIMÉ en anglais aussi (parité)', async () => {
    mockRollups()
    renderPanel('all', 'en')
    expect((await screen.findAllByText('~$0.00750 estimated')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('$0.0131 derived').length).toBeGreaterThan(0)
  })
})

describe('UsagePanel — états', () => {
  it('état vide quand aucune donnée', async () => {
    vi.mocked(getUsageRollup).mockResolvedValue({ group_by: 'cost_basis', scope: 'all', rows: [] })
    renderPanel()
    expect(await screen.findByText('Aucune donnée d’usage dans cette portée.')).toBeTruthy()
  })

  it('télémétrie indisponible sur échec', async () => {
    vi.mocked(getUsageRollup).mockRejectedValue(new Error('boom'))
    renderPanel()
    expect(await screen.findByText('Télémétrie indisponible')).toBeTruthy()
  })
})

describe('UsagePanel — drill-down borné', () => {
  it('cliquer un fournisseur ouvre une liste bornée puis le détail model_call', async () => {
    mockRollups()
    vi.mocked(getUsageExecutions).mockResolvedValue({
      executions: [{
        execution_id: 'EX1', kind: 'report', status: 'ok', project_id: 'P',
        provider_primary: 'openai', model_primary: 'gpt-5.6-luna', started_at: '2026-08-09T10:00:00+00:00',
        ended_at: null, wall_clock_s: 4, n_calls: 2, in_tok: 5000, out_tok: 1200, audio_seconds: null,
        cost: 0.0131, cost_currency: 'USD', cost_basis: 'cloud_derived',
      }],
      limit: 10, offset: 0, has_more: false, scope: 'all',
    })
    vi.mocked(getUsageExecutionDetail).mockResolvedValue({
      available: true, calls: [{
        seq: 1, stage: 'extract', provider: 'openai', model: 'gpt-5.6-luna', latency_s: 16.3,
        status: 'ok', usage_source: 'provider_reported', in_tok: 4000, out_tok: 900, cached_tok: 0,
        reasoning_tok: 120, cost: 0.009, cost_currency: 'USD', cost_basis: 'cloud_derived',
      }],
    })
    renderPanel('P')
    const openaiBar = await screen.findByRole('button', { name: /openai/ })
    await userEvent.click(openaiBar)
    // La liste d'exécutions bornée est demandée pour ce fournisseur, dans la portée projet.
    expect(getUsageExecutions).toHaveBeenCalledWith('P', { provider: 'openai' }, 10, 0)
    const exec = await screen.findByText('gpt-5.6-luna')
    // Ouvrir l'exécution charge le détail forensique (paresseux).
    await userEvent.click(exec)
    // '16.3s' (latence) est unique à la table forensique — on ancre dessus.
    const latency = await screen.findByText('16.3s')
    expect(getUsageExecutionDetail).toHaveBeenCalledWith('P', 'EX1')
    expect(within(latency.closest('tr')!).getByText('extract')).toBeTruthy()
  })
})
