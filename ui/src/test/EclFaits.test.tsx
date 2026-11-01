import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { EclFaits } from '../components/EclFaits'
import { LangProvider } from '../components/LangProvider'
import { getConfig, getFaitsReunion, getJobStatus, relancerFaitsReunion, type FaitEcl } from '../api'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getFaitsReunion: vi.fn(), getConfig: vi.fn(), getJobStatus: vi.fn(), relancerFaitsReunion: vi.fn() }
})

const LOCAL: FaitEcl = {
  id: 'f1', document_id: 'D1', document: 'reunion.md', type: 'decision', horodatage: '00:01:02',
  secondes: 62, citation: 'on fixe le prix à 25 euros', enonce: 'Le prix est fixé à 25 euros.',
  enonce_statut: 'reformulé', porteur: null, locuteur: 'Alice', modele: 'granite', mode: 'local',
  a_verifier: true,
}
const CLOUD: FaitEcl = {
  ...LOCAL, id: 'f2', type: 'action', citation: 'Bruno envoie le devis', enonce: 'Bruno envoie le devis.',
  porteur: 'Bruno', modele: 'claude-haiku-5-5', mode: 'cloud', a_verifier: false,
}

beforeEach(() => {
  vi.mocked(getFaitsReunion).mockReset()
  vi.mocked(getConfig).mockResolvedValue({ llm_providers: ['mistral', 'mlx', 'openai'], local_providers: ['mlx'],
    default_llm_provider: 'mlx' } as never)
})

describe('EclFaits', () => {
  it('affiche la citation d’abord, l’énoncé marqué reformulé, et « à vérifier » en local', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [LOCAL, CLOUD] })
    render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('« on fixe le prix à 25 euros »')).toBeTruthy()
    expect(screen.getByText('Le prix est fixé à 25 euros.')).toBeTruthy()
    expect(screen.getAllByText('Reformulé')).toHaveLength(2)
    expect(screen.getAllByText(/À vérifier/)).toHaveLength(1)                 // le fait cloud n'en porte pas
    expect(screen.queryByText(/précision/i)).toBeNull()                       // aucun taux de précision
    expect(screen.getByText('Porteur : Bruno')).toBeTruthy()
    expect(screen.getByText('Décision')).toBeTruthy()
    expect(getFaitsReunion).toHaveBeenCalledWith('M1', 'all')                  // portée propagée
  })

  it('n’affiche aucun taux, même si un serveur plus ancien en rend un', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [{ ...LOCAL, taux: 0.62 } as FaitEcl] })
    const { container } = render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('À vérifier')).toBeTruthy()
    expect(container.querySelector('.ecl-faits')?.textContent).not.toMatch(/62|%|précision/i)
  })

  it('sans taux mesuré, aucun pourcentage : « à vérifier » en local, rien en cloud', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [LOCAL, { ...CLOUD, modele: 'gpt-5.6-luna' }] })
    const { container } = render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('À vérifier')).toBeTruthy()
    expect(container.querySelector('.ecl-faits')?.textContent).not.toMatch(/%/)
    expect(screen.queryByText(/précision/i)).toBeNull()
  })

  it('un fait cloud ne montre ni taux de précision ni « à vérifier »', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [{ ...CLOUD, modele: 'claude-sonnet-5-5', taux: 0.67 } as FaitEcl] })
    const { container, unmount } = render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('modèle : claude-sonnet-5-5')).toBeTruthy()
    expect(container.querySelector('.ecl-faits')?.textContent).not.toMatch(/67|%|précision/i)
    expect(screen.queryByText(/À vérifier/)).toBeNull()
    unmount()
    const anglais = render(<LangProvider initiale="en"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('model: claude-sonnet-5-5')).toBeTruthy()
    expect(anglais.container.querySelector('.ecl-faits')?.textContent).not.toMatch(/67|%|precision/i)
  })

  it('Anthropic est proposé par défaut pour relancer, le local reste au choix', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [LOCAL] })
    vi.mocked(getConfig).mockResolvedValue({ llm_providers: ['mistral', 'mlx', 'openai', 'anthropic'],
      local_providers: ['mlx'], default_llm_provider: 'mlx', llm_ready: { anthropic: true } } as never)
    vi.mocked(relancerFaitsReunion).mockResolvedValue({ job_id: 'J1' })
    vi.mocked(getJobStatus).mockResolvedValue({ id: 'J1', status: 'done', progress: 1, message: '', result: null, error: null })
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    await screen.findByRole('option', { name: 'Anthropic Claude (cloud)' })
    const choix = screen.getByRole('combobox') as HTMLSelectElement
    expect(choix.value).toBe('anthropic')
    expect(screen.getByRole('option', { name: 'MLX (local)' })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Relancer l’extraction des faits' }))
    expect(confirmer).toHaveBeenCalled()
    expect(relancerFaitsReunion).toHaveBeenCalledWith('M1', 'all', 'anthropic')
    confirmer.mockRestore()
  })

  it('la confirmation cloud nomme le fournisseur par son libellé, et le sélecteur a le style commun', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [LOCAL] })
    vi.mocked(getConfig).mockResolvedValue({ llm_providers: ['mlx', 'anthropic'], local_providers: ['mlx'],
      default_llm_provider: 'mlx', llm_ready: { anthropic: true } } as never)
    vi.mocked(relancerFaitsReunion).mockClear()
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    await screen.findByRole('option', { name: 'Anthropic Claude (cloud)' })
    const choix = screen.getByRole('combobox') as HTMLSelectElement
    expect(choix.className).toBe('mini-select')
    await userEvent.click(screen.getByRole('button', { name: 'Relancer l’extraction des faits' }))
    expect(confirmer).toHaveBeenCalledWith(
      'Le transcript de cette réunion partira chez Anthropic Claude (cloud). Confirmer ?')
    expect(relancerFaitsReunion).not.toHaveBeenCalled()                         // refusée : rien ne part
    confirmer.mockRestore()
  })

  it('sans clé Anthropic, ou en ultra-confidentiel, le défaut reste local', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [LOCAL] })
    vi.mocked(getConfig).mockResolvedValue({ llm_providers: ['mlx', 'anthropic'], local_providers: ['mlx'],
      default_llm_provider: 'mlx', llm_ready: { anthropic: false } } as never)
    const { unmount } = render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    await screen.findByRole('option', { name: 'Anthropic Claude (cloud)' })
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('mlx')
    unmount()
    vi.mocked(getConfig).mockResolvedValue({ llm_providers: ['mlx', 'anthropic'], local_providers: ['mlx'],
      default_llm_provider: 'mlx', llm_ready: { anthropic: true } } as never)
    render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} ultra /></LangProvider>)
    await screen.findByRole('option', { name: 'MLX (local)' })
    expect(screen.queryByRole('option', { name: 'Anthropic Claude (cloud)' })).toBeNull()
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('mlx')
  })

  it('l’horodatage ouvre le passage du transcript', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [LOCAL] })
    const onSource = vi.fn()
    render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={onSource} /></LangProvider>)
    await userEvent.click(await screen.findByRole('button', { name: 'Voir le passage à 00:01:02' }))
    expect(onSource).toHaveBeenCalledWith('reunion.md', '00:01:02')
  })

  it('dit qu’une réunion n’a aucun fait, en français et en anglais', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [] })
    const { unmount } = render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('Aucun fait relevé pour cette réunion.')).toBeTruthy()
    unmount()
    render(<LangProvider initiale="en"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('No facts recorded for this meeting.')).toBeTruthy()
  })

  it('en anglais, la mention et le type sont traduits', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [LOCAL] })
    render(<LangProvider initiale="en"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('To verify')).toBeTruthy()
    expect(screen.queryByText(/precision/i)).toBeNull()
    expect(screen.getByText('Decision')).toBeTruthy()
    expect(screen.getByText('Reworded')).toBeTruthy()
  })

  it('une erreur de lecture est dite, pas masquée', async () => {
    vi.mocked(getFaitsReunion).mockImplementation(() => Promise.reject(new Error('404')))
    render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText(/Faits illisibles/)).toBeTruthy()
  })

  it('montre la suite de la citation à part, le mode et le fournisseur', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [{
      ...LOCAL, citation: 'on garde le rouge', affiche: 'on garde le rouge oui, d’accord pour tous', fournisseur: 'mlx' }] })
    render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('« on garde le rouge »')).toBeTruthy()
    expect(screen.getByText(/… oui, d’accord pour tous/)).toBeTruthy()               // la suite, distincte
    expect(screen.getByText('local · mlx')).toBeTruthy()
  })

  it('relance avec un fournisseur cloud après confirmation, puis recharge', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [LOCAL] })
    vi.mocked(relancerFaitsReunion).mockResolvedValue({ job_id: 'J1' })
    vi.mocked(getJobStatus).mockResolvedValue({ id: 'J1', status: 'done', progress: 1, message: '', result: null, error: null })
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    await userEvent.selectOptions(await screen.findByRole('combobox'), 'openai')
    await userEvent.click(screen.getByRole('button', { name: 'Relancer l’extraction des faits' }))
    expect(confirmer).toHaveBeenCalledWith(expect.stringContaining('OpenAI (cloud)'))      // le libellé, pas l’identifiant
    expect(relancerFaitsReunion).toHaveBeenCalledWith('M1', 'all', 'openai')
    expect(await screen.findByText('L’extraction précédente est gardée, datée.')).toBeTruthy()
    expect(getFaitsReunion).toHaveBeenCalledTimes(2)
    confirmer.mockRestore()
  })

  it('un refus de confirmation ne relance rien ; en local, pas de confirmation', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [LOCAL] })
    vi.mocked(relancerFaitsReunion).mockResolvedValue({ job_id: 'J1' })
    vi.mocked(getJobStatus).mockResolvedValue({ id: 'J1', status: 'done', progress: 1, message: '', result: null, error: null })
    const confirmer = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<LangProvider initiale="en"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    await userEvent.selectOptions(await screen.findByRole('combobox'), 'mistral')
    await userEvent.click(screen.getByRole('button', { name: 'Re-extract the facts' }))
    expect(relancerFaitsReunion).not.toHaveBeenCalled()
    await userEvent.selectOptions(screen.getByRole('combobox'), 'mlx')
    confirmer.mockClear()
    await userEvent.click(screen.getByRole('button', { name: 'Re-extract the facts' }))
    expect(confirmer).not.toHaveBeenCalled()
    expect(relancerFaitsReunion).toHaveBeenCalledWith('M1', 'all', 'mlx')
    confirmer.mockRestore()
  })

  it('un modèle local (repli sur le modèle du CR) : « à vérifier » et ce modèle-là', async () => {
    const repli = { ...LOCAL, modele: 'mlx-community/Qwen3-4B-Instruct-2507-4bit', fournisseur: 'mlx' }
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [repli] })
    const { container } = render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('À vérifier')).toBeTruthy()
    expect(screen.getByText('modèle : mlx-community/Qwen3-4B-Instruct-2507-4bit')).toBeTruthy()   // le modèle exécuté
    expect(container.querySelector('.ecl-faits')?.textContent).not.toMatch(/%/)              // aucun taux d’emprunt
  })

  it('les faits périmés viennent à part, marqués, avec leur motif, en français et en anglais', async () => {
    const perime: FaitEcl = { ...LOCAL, id: 'f9', citation: 'on fixe le prix à 25 euros', enonce: 'Prix fixé à 25 euros.',
      statut: 'perime', motif_peremption: 'citation_absente' }
    const actuel: FaitEcl = { ...LOCAL, citation: 'on fixe le prix à 35 euros', enonce: 'Le prix est fixé à 35 euros.' }
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [actuel], perimes: [perime] })
    const { container, unmount } = render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    const section = await screen.findByRole('region', { name: /Faits périmés/ })
    expect(section.textContent).toContain('Périmé')
    expect(section.textContent).toContain('la citation ne figure plus dans le transcript')
    expect(section.textContent).toContain('Prix fixé à 25 euros.')
    expect(section.textContent).not.toContain('35 euros')                                       // l’actuel n’y est pas
    const actuels = [...container.querySelectorAll('.ecl-fait:not(.ecl-fait-perime)')]
    expect(actuels).toHaveLength(1)
    expect(actuels[0].textContent).toContain('Le prix est fixé à 35 euros.')
    expect(actuels[0].textContent).not.toContain('25 euros')                                    // jamais parmi les actuels
    expect(screen.getAllByText('Reformulé')).toHaveLength(1)
    unmount()
    render(<LangProvider initiale="en"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    const enAnglais = await screen.findByRole('region', { name: /Stale facts/ })
    expect(enAnglais.textContent).toContain('Stale')
    expect(enAnglais.textContent).toContain('the quote is no longer in the transcript')
  })

  it('sans fait actuel, la réunion le dit et montre encore ses faits périmés', async () => {
    const perime: FaitEcl = { ...LOCAL, statut: 'perime', motif_peremption: 'contenance' }
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [], perimes: [perime] })
    render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('Aucun fait relevé pour cette réunion.')).toBeTruthy()
    const section = screen.getByRole('region', { name: /Faits périmés/ })
    expect(section.textContent).toContain('l’énoncé n’est plus porté par la citation')
  })

  it('en lecture seule, aucune relance n’est proposée', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [LOCAL] })
    render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="shared" onSource={() => {}} lectureSeule /></LangProvider>)
    expect(await screen.findByText('« on fixe le prix à 25 euros »')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Relancer l’extraction des faits' })).toBeNull()
    expect(getConfig).not.toHaveBeenCalled()
  })

  it('une réunion sans faits propose la première extraction', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [] })
    render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('Aucun fait relevé pour cette réunion.')).toBeTruthy()
    expect(await screen.findByRole('button', { name: 'Relancer l’extraction des faits' })).toBeTruthy()
  })

  it('une extraction partielle est marquée, avec ses tranches en échec', async () => {
    const partielle = { tranches_en_echec: 1, tranches: 3 }
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [{ ...LOCAL, partielle }] })
    const { unmount } = render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText(/Extraction partielle : 1 tranche sur 3 en échec/)).toBeTruthy()
    unmount()
    render(<LangProvider initiale="en"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText(/Partial extraction: 1 of 3 chunks failed/)).toBeTruthy()
  })

  it('une extraction complète ne porte pas la marque', async () => {
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [{ ...LOCAL, partielle: null }] })
    render(<LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)
    expect(await screen.findByText('« on fixe le prix à 25 euros »')).toBeTruthy()
    expect(screen.queryByText(/Extraction partielle/)).toBeNull()
  })
})
