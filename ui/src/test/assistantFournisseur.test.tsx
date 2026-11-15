/**
 * L'Assistant répond avec le fournisseur retenu par le COMPTE, s'il est prêt ; sinon avec le local. Plus rien ne se lit dans le
 * navigateur : un ancien choix gardé par le navigateur est ignoré. Le local est l'appel à deux arguments (comme avant) ;
 * seul un fournisseur en ligne retenu et prêt ajoute le troisième.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AppConfig } from '../api'
import { ask, getConfig, getReglagesTraitement } from '../api'
import { AskPanel } from '../components/AskPanel'
import { LangProvider } from '../components/LangProvider'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, ask: vi.fn(), getConfig: vi.fn(), getReglagesTraitement: vi.fn() }
})

const CONFIG = {
  llm_etats: {
    mlx: { lieu: 'local', etat: 'pret' }, groq: { lieu: 'en_ligne', etat: 'pret' }, mistral: { lieu: 'en_ligne', etat: 'non_configure' },
  },
  local_providers: ['mlx'], default_llm_provider: 'mlx',
} as unknown as AppConfig

const retenu = (fournisseur: string) => ({
  reglages: { fournisseur, langue: '', resultats: { compte_rendu: true, titre_resume: true, locuteurs: false } } })

const PORTEE = { mode: 'project', project_id: 'project-id' }

async function demander() {
  const user = userEvent.setup()
  render(<LangProvider initiale="fr"><AskPanel viewScope="project-id" scopeLabel="Client" /></LangProvider>)
  await user.type(screen.getByPlaceholderText('Poser une question sur vos transcripts…'), 'Question')
  await user.click(screen.getByRole('button', { name: 'Demander' }))
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(ask).mockReset().mockResolvedValue({ answer: 'Réponse', sources: [] })
  vi.mocked(getConfig).mockReset().mockResolvedValue(CONFIG)
  vi.mocked(getReglagesTraitement).mockReset().mockResolvedValue({ reglages: null })
})

describe('le fournisseur de l\'Assistant', () => {
  it('un fournisseur retenu par le compte ET prêt est celui de l\'Assistant', async () => {
    vi.mocked(getReglagesTraitement).mockResolvedValue(retenu('groq'))
    await demander()
    await vi.waitFor(() => expect(ask).toHaveBeenCalledWith('Question', PORTEE, 'groq'))
  })

  it('un fournisseur retenu mais plus prêt (clé retirée) ou disparu : le local', async () => {
    for (const absent of ['mistral', 'disparu']) {
      vi.mocked(ask).mockClear()
      vi.mocked(getReglagesTraitement).mockResolvedValue(retenu(absent))
      const { unmount } = render(<LangProvider initiale="fr"><AskPanel viewScope="project-id" scopeLabel="Client" /></LangProvider>)
      await userEvent.type(screen.getByPlaceholderText('Poser une question sur vos transcripts…'), 'Question')
      await userEvent.click(screen.getByRole('button', { name: 'Demander' }))
      await vi.waitFor(() => expect(ask).toHaveBeenCalledTimes(1))
      expect(ask).toHaveBeenLastCalledWith('Question', PORTEE)                         // le local : l'appel à deux arguments
      unmount()
    }
  })

  it('aucun réglage retenu : le local', async () => {
    await demander()
    await vi.waitFor(() => expect(ask).toHaveBeenCalledTimes(1))
    expect(ask).toHaveBeenLastCalledWith('Question', PORTEE)
  })

  it('une configuration ou des réglages illisibles : le local, sans bloquer la question', async () => {
    vi.mocked(getConfig).mockRejectedValue(new Error('hors ligne'))
    await demander()
    await vi.waitFor(() => expect(ask).toHaveBeenCalledTimes(1))
    expect(ask).toHaveBeenLastCalledWith('Question', PORTEE)
  })

  it('un ancien choix gardé par le navigateur est IGNORÉ : seul le compte décide', async () => {
    localStorage.setItem('memoglyph.llmProvider', 'openai')
    await demander()
    await vi.waitFor(() => expect(ask).toHaveBeenCalledTimes(1))
    expect(ask).toHaveBeenLastCalledWith('Question', PORTEE)
  })
})
