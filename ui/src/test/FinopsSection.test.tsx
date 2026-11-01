import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FinopsSection } from '../components/FinopsSection'
import { LangProvider } from '../components/LangProvider'
import { getFinopsSummary, getFinopsExecution } from '../api'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getFinopsSummary: vi.fn(), getFinopsExecution: vi.fn() }
})

const CLOUD = {
  available: true, execution_id: 'EX1', kind: 'report', status: 'ok',
  provider: 'OpenAI', model: 'gpt-5.6-luna', cost: 0.0125, currency: 'USD',
  cost_basis: 'cloud_derived', pricing_provenance: 'official_published',
  calls: 4, in_tok: 1500, out_tok: 1000, tokens: 2500, audio_seconds: null, wall_clock_s: 37,
  started_at: 't', ended_at: 't',
}

beforeEach(() => {
  vi.mocked(getFinopsSummary).mockResolvedValue({ available: false })
  vi.mocked(getFinopsExecution).mockResolvedValue({ available: true, calls: [] })
})

describe('FinopsSection', () => {
  it('affiche un coût nuage DÉRIVÉ, provider/model verbatim, jetons et temps', async () => {
    vi.mocked(getFinopsSummary).mockResolvedValue(CLOUD)
    render(<FinopsSection name="r.md" viewScope="all" label="Compte-rendu" />)
    expect(await screen.findByText('OpenAI')).toBeTruthy()
    expect(screen.getByText('gpt-5.6-luna')).toBeTruthy()
    expect(screen.getByText('$0.0125 dérivé')).toBeTruthy()   // cloud_derived => dérivé
    expect(screen.getByText('2.5k')).toBeTruthy()
    expect(screen.getByText('37 s')).toBeTruthy()
    expect(getFinopsSummary).toHaveBeenCalledWith('r.md', 'all')   // scope propagé
  })

  it('marque un coût local ESTIMÉ avec ~', async () => {
    vi.mocked(getFinopsSummary).mockResolvedValue({
      ...CLOUD, provider: 'MLX', model: 'Qwen3-4B', cost: 0.00024, cost_basis: 'local_imputed' })
    render(<FinopsSection name="r.md" viewScope="all" label="CR" />)
    expect(await screen.findByText('~$0.00024 estimé')).toBeTruthy()
  })

  it('NULL => — non tarifé (jamais 0)', async () => {
    vi.mocked(getFinopsSummary).mockResolvedValue({ ...CLOUD, cost: null, cost_basis: null })
    render(<FinopsSection name="r.md" viewScope="all" label="CR" />)
    expect(await screen.findByText('— non tarifé')).toBeTruthy()
    expect(screen.queryByText(/\$0/)).toBeNull()
  })

  it('STT : montre la durée audio, pas des jetons', async () => {
    vi.mocked(getFinopsSummary).mockResolvedValue({
      available: true, execution_id: 'S1', kind: 'stt', provider: 'whisper',
      model: 'whisper-large-v3-turbo', cost: 0.00075, currency: 'USD',
      cost_basis: 'local_imputed', calls: 0, tokens: null, audio_seconds: 90, wall_clock_s: 4,
    })
    render(<FinopsSection name="t.md" viewScope="all" label="Transcription" />)
    expect(await screen.findByText('1m 30s')).toBeTruthy()   // audio 90s
  })

  it('exécution absente => rien affiché', async () => {
    vi.mocked(getFinopsSummary).mockResolvedValue({ available: false })
    const { container } = render(<FinopsSection name="r.md" viewScope="all" label="CR" />)
    // rien du bloc finops (pas de label)
    await Promise.resolve()
    expect(container.querySelector('.finops-block')).toBeNull()
  })

  it('lecture en échec => état indisponible', async () => {
    vi.mocked(getFinopsSummary).mockRejectedValue(new Error('boom'))
    render(<FinopsSection name="r.md" viewScope="all" label="CR" />)
    expect(await screen.findByText('Indisponible')).toBeTruthy()
  })

  it('« Voir le détail » charge les appels À L\'OUVERTURE (divulgation progressive)', async () => {
    vi.mocked(getFinopsSummary).mockResolvedValue(CLOUD)
    vi.mocked(getFinopsExecution).mockResolvedValue({
      available: true, calls: [
        { seq: 1, stage: 'extract', provider: 'openai', model: 'gpt-5.6-luna', latency_s: 16.3,
          status: 'ok', usage_source: 'provider_reported', in_tok: 5480, out_tok: 1240,
          cached_tok: 512, reasoning_tok: 210, cost: 0.00249, cost_currency: 'USD', cost_basis: 'cloud_derived' },
      ] })
    render(<FinopsSection name="r.md" viewScope="all" label="CR" />)
    const details = await screen.findByText('Voir le détail d’exécution')
    expect(getFinopsExecution).not.toHaveBeenCalled()   // paresseux : pas avant l'ouverture
    await userEvent.click(details)
    const row = await screen.findByText('extract')
    expect(getFinopsExecution).toHaveBeenCalledWith('r.md', 'EX1', 'all')
    const tr = row.closest('tr')!
    expect(within(tr).getByText('16.3s')).toBeTruthy()
    // Les jetons en cache portent un LIBELLÉ, jamais un « $ » qui se lit comme une somme.
    expect(within(tr).getByText('5.5k/1.2k (512 en cache)')).toBeTruthy()
  })
})

describe('FinopsSection — profilage d’un transcript', () => {
  const STT = {
    available: true, execution_id: 'S1', kind: 'stt', provider: 'whisper',
    model: 'whisper-large-v3-turbo', cost: 0.00075, currency: 'USD',
    cost_basis: 'local_imputed', calls: 0, tokens: null, audio_seconds: 90, wall_clock_s: 4,
  }
  const PROFIL = {
    ...CLOUD, execution_id: 'P1', kind: 'profile', provider: 'mistral',
    model: 'ministral-14b-2512', cost: 0.0009, calls: 1, in_tok: 1000, out_tok: 200, tokens: 1200,
    wall_clock_s: 3,
  }

  it('montre le profilage sous la transcription : fournisseur, modèle, jetons, coût', async () => {
    vi.mocked(getFinopsSummary).mockResolvedValue({ ...STT, profile: PROFIL })
    const { container } = render(<FinopsSection name="t.md" viewScope="all" label="Transcription" />)
    const titre = await screen.findByText('Profilage')
    expect(container.querySelectorAll('.finops-block')).toHaveLength(2)
    const bloc = titre.closest('.finops-block') as HTMLElement
    expect(within(bloc).getByText('mistral')).toBeTruthy()
    expect(within(bloc).getByText('ministral-14b-2512')).toBeTruthy()
    expect(within(bloc).getByText('1.2k')).toBeTruthy()
    expect(within(bloc).getByText('$0.00090 dérivé')).toBeTruthy()
    // La transcription garde son propre bloc, avec la durée audio.
    expect(screen.getByText('Transcription')).toBeTruthy()
    expect(screen.getByText('1m 30s')).toBeTruthy()
  })

  it('le détail du profilage charge SA propre exécution', async () => {
    vi.mocked(getFinopsSummary).mockResolvedValue({ ...STT, profile: PROFIL })
    render(<FinopsSection name="t.md" viewScope="all" label="Transcription" />)
    const bloc = (await screen.findByText('Profilage')).closest('.finops-block') as HTMLElement
    await userEvent.click(within(bloc).getByText('Voir le détail d’exécution'))
    expect(getFinopsExecution).toHaveBeenCalledWith('t.md', 'P1', 'all')
  })

  it('un profilage sans transcription liée s’affiche seul', async () => {
    vi.mocked(getFinopsSummary).mockResolvedValue({ available: false, profile: PROFIL })
    const { container } = render(<FinopsSection name="t.md" viewScope="all" label="Transcription" />)
    expect(await screen.findByText('Profilage')).toBeTruthy()
    expect(container.querySelectorAll('.finops-block')).toHaveLength(1)
    expect(screen.queryByText('Transcription')).toBeNull()
  })

  it('libellé EN', async () => {
    vi.mocked(getFinopsSummary).mockResolvedValue({ ...STT, profile: PROFIL })
    render(<LangProvider initiale="en"><FinopsSection name="t.md" viewScope="all" label="Transcription" /></LangProvider>)
    expect(await screen.findByText('Profiling')).toBeTruthy()
    expect(screen.getByText('$0.00090 derived')).toBeTruthy()
  })
})

describe('FinopsSection — sémantique EN (parité FR/EN)', () => {
  beforeEach(() => {
    vi.mocked(getFinopsSummary).mockResolvedValue({ available: false })
  })

  const renderEN = (row: Record<string, unknown>) => {
    vi.mocked(getFinopsSummary).mockResolvedValue(row as never)
    return render(
      <LangProvider initiale="en">
        <FinopsSection name="r.md" viewScope="all" label="Report" />
      </LangProvider>,
    )
  }

  it('cloud_derived => derived', async () => {
    renderEN(CLOUD)
    expect(await screen.findByText('$0.0125 derived')).toBeTruthy()
    expect(screen.getByText('gpt-5.6-luna')).toBeTruthy()   // modèle VERBATIM, non traduit
  })

  it('local_imputed => estimated (~)', async () => {
    renderEN({ ...CLOUD, cost: 0.00024, cost_basis: 'local_imputed' })
    expect(await screen.findByText('~$0.00024 estimated')).toBeTruthy()
  })

  it('NULL => unpriced, jamais zéro', async () => {
    renderEN({ ...CLOUD, cost: null, cost_basis: null })
    expect(await screen.findByText('— unpriced')).toBeTruthy()
    expect(screen.queryByText(/\$0/)).toBeNull()
  })
})
