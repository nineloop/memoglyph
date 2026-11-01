import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EclFaits } from '../components/EclFaits'
import { LangProvider } from '../components/LangProvider'
import { getConfig, getFaitsReunion, type FaitEcl } from '../api'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getFaitsReunion: vi.fn(), getConfig: vi.fn(), getJobStatus: vi.fn(), relancerFaitsReunion: vi.fn() }
})

// Un fait extrait par un serveur compatible OpenAI hébergé hors de la machine.
const PRIVE: FaitEcl = {
  id: 'f1', document_id: 'D1', document: 'reunion.md', type: 'decision', horodatage: '00:01:02',
  secondes: 62, citation: 'on fixe le prix à 25 euros', enonce: 'Le prix est fixé à 25 euros.',
  enonce_statut: 'reformulé', porteur: null, locuteur: 'Alice', modele: 'qwen3:8b', mode: 'cloud',
  a_verifier: false, fournisseur: 'compatible',
}

const rendre = () => render(
  <LangProvider initiale="fr"><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>)

beforeEach(() => {
  vi.mocked(getFaitsReunion).mockReset()
  vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [] })
})

describe('Serveur compatible OpenAI dans le sélecteur des faits', () => {
  it('configuré (annoncé par le backend) : proposé sous son libellé, « cloud privé » hors de la machine', async () => {
    vi.mocked(getConfig).mockResolvedValue({ llm_providers: ['mlx', 'compatible'], local_providers: ['mlx'],
      private_cloud_providers: ['compatible'], default_llm_provider: 'mlx' } as never)
    rendre()
    expect(await screen.findByRole('option', { name: 'Serveur compatible OpenAI (Cloud privé)' })).toBeTruthy()
  })

  it('sur la machine : proposé comme local, sans « cloud privé »', async () => {
    vi.mocked(getConfig).mockResolvedValue({ llm_providers: ['mlx', 'compatible'], local_providers: ['mlx', 'compatible'],
      private_cloud_providers: [], default_llm_provider: 'mlx' } as never)
    rendre()
    expect(await screen.findByRole('option', { name: 'Serveur compatible OpenAI' })).toBeTruthy()
  })

  it('non configuré (absent de llm_providers) : jamais proposé', async () => {
    vi.mocked(getConfig).mockResolvedValue({ llm_providers: ['mlx', 'mistral'], local_providers: ['mlx'],
      private_cloud_providers: [], default_llm_provider: 'mlx' } as never)
    rendre()
    expect(await screen.findByRole('option', { name: 'MLX (local)' })).toBeTruthy()
    expect(screen.queryByRole('option', { name: /compatible/i })).toBeNull()
  })

  it('un fait du serveur compatible distant dit « cloud privé », sans mesure de précision', async () => {
    vi.mocked(getConfig).mockResolvedValue({ llm_providers: ['mlx', 'compatible'], local_providers: ['mlx'],
      private_cloud_providers: ['compatible'], default_llm_provider: 'mlx' } as never)
    vi.mocked(getFaitsReunion).mockResolvedValue({ meeting_id: 'M1', faits: [PRIVE] })
    rendre()
    expect(await screen.findByText(/cloud privé/)).toBeTruthy()
    expect(screen.queryByText(/précision/)).toBeNull()
  })
})
