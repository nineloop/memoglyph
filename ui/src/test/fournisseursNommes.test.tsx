// OpenRouter et Groq dans l'interface.
// - ils apparaissent SOUS LEUR NOM (jamais « Cloud privé ») partout où les fournisseurs prêts apparaissent, en français et en
//   anglais ; non prêts, ils sont absents ;
// - un refus « modèle gratuit » (403 à corps plat) se dit par sa phrase, dans la langue de l'interface, pour la régénération,
//   la transcription, la relance des faits et l'assistant ; tout autre refus reste comme avant ; un 429 de travail reste « occupé ».
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ErreurAvecCode, ModeleGratuitError, ask, getCloudUltra, getConfig, getFaitsReunion, getJobStatus, getTranscript,
  lancerCompteRendu, relancerFaitsReunion, streamJob, submitTranscribe, type AppConfig,
} from '../api'
import { AskPanel } from '../components/AskPanel'
import { EclFaits } from '../components/EclFaits'
import { LangProvider } from '../components/LangProvider'
import { RegenererCompteRendu } from '../components/RegenererCompteRendu'
import { TranscribePanel } from '../components/TranscribePanel'
import { TraitementsContext } from '../lib/traitementsContext'
import { CATALOGUES } from '../lib/messages'
import { avecConfirmation, confirmer as confirmerLaFenetre } from './confirmation'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual, ask: vi.fn(), getCloudUltra: vi.fn(), getConfig: vi.fn(), getFaitsReunion: vi.fn(), getJobStatus: vi.fn(),
    getTranscript: vi.fn(), lancerCompteRendu: vi.fn(), relancerFaitsReunion: vi.fn(), streamJob: vi.fn(), submitTranscribe: vi.fn(),
  }
})

const PRETS = {
  llm_providers: ['mlx', 'openrouter', 'groq'],
  local_providers: ['mlx'],
  private_cloud_providers: [] as string[],
  default_llm_provider: 'mlx',
  llm_ready: { mlx: true, openrouter: true, groq: true },
  llm_models: { mlx: 'Qwen3-8B', openrouter: 'auteur/modele', groq: 'un-modele' },
}
const NON_PRETS = { ...PRETS, llm_providers: ['mlx'], llm_ready: { mlx: true, openrouter: false, groq: false } }

const PHRASE = {
  fr: CATALOGUES.fr['job.step.error.freeModelRestricted'],
  en: CATALOGUES.en['job.step.error.freeModelRestricted'],
}

beforeEach(() => {
  vi.mocked(getConfig).mockReset().mockResolvedValue(PRETS as never)
  vi.mocked(getCloudUltra).mockReset().mockResolvedValue({ autorise: false } as never)
  vi.mocked(lancerCompteRendu).mockReset().mockResolvedValue({ job_id: 'j1' })
  vi.mocked(relancerFaitsReunion).mockReset().mockResolvedValue({ job_id: 'J1' })
  vi.mocked(getFaitsReunion).mockReset().mockResolvedValue({ meeting_id: 'M1', faits: [] })
  vi.mocked(getJobStatus).mockReset()
  vi.mocked(ask).mockReset()
  vi.mocked(getTranscript).mockReset()
  vi.mocked(submitTranscribe).mockReset()
  vi.mocked(streamJob).mockReset()
  localStorage.removeItem('memoglyph.llmProvider')
})

const gratuit = () => new ModeleGratuitError()

// ---------- Régénérer ----------

function monterRegenerer(langue: 'fr' | 'en' = 'fr') {
  return render(
    <LangProvider initiale={langue}>
      <TraitementsContext.Provider value={{ liste: null, rafraichir: vi.fn(), suivre: vi.fn() }}>
        <RegenererCompteRendu name="reunion.md" documentScope="all" />
      </TraitementsContext.Provider>
    </LangProvider>, { wrapper: avecConfirmation })
}

describe('Régénérer : OpenRouter et Groq', () => {
  it.each([['fr', 'Régénérer'], ['en', 'Regenerate']] as const)('prêts : proposés sous leur nom, jamais « cloud privé » (%s)', async (langue, bouton) => {
    monterRegenerer(langue)
    await userEvent.click(screen.getByRole('button', { name: bouton }))
    expect(await screen.findByRole('radio', { name: /OpenRouter/ })).toBeTruthy()
    expect(screen.getByRole('radio', { name: /Groq/ })).toBeTruthy()
    expect(screen.queryByText(/cloud privé|private cloud/i)).toBeNull()
  })

  it('non prêts : absents', async () => {
    vi.mocked(getConfig).mockResolvedValue(NON_PRETS as never)
    monterRegenerer()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    await screen.findAllByRole('radio')
    expect(screen.queryByRole('radio', { name: /OpenRouter|Groq/ })).toBeNull()
  })

  it.each([['fr', 'Régénérer', 'Régénérer ce compte rendu'], ['en', 'Regenerate', 'Regenerate this report']] as const)(
    'un refus « modèle gratuit » se dit par sa phrase (%s), sans la phrase du nuage ni texte brut', async (langue, ouvrir, lancer) => {
      vi.mocked(lancerCompteRendu).mockRejectedValue(gratuit())
      monterRegenerer(langue)
      await userEvent.click(screen.getByRole('button', { name: ouvrir }))
      await userEvent.click(await screen.findByRole('radio', { name: /OpenRouter/ }))
      await userEvent.click(screen.getByRole('button', { name: lancer }))
      await confirmerLaFenetre(userEvent)
      const alerte = await screen.findByRole('alert')
      expect(alerte.textContent).toBe(PHRASE[langue])
      expect(alerte.textContent).not.toMatch(/HTTP|detail/)
      expect(alerte.textContent).not.toBe(CATALOGUES[langue]['regenerer.refused.confidential'])   // pas la phrase du refus du nuage
    })

  it('un autre 403 reste la phrase de l’ultra-confidentiel ; un 429 reste « occupé », jamais le quota', async () => {
    monterRegenerer()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    await userEvent.click(await screen.findByRole('radio', { name: /OpenRouter/ }))
    vi.mocked(lancerCompteRendu).mockRejectedValueOnce(new Error('HTTP 403: {"detail":"Un document ultra-confidentiel ne part pas vers un fournisseur cloud"}'))
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer ce compte rendu' }))
    await confirmerLaFenetre(userEvent)
    expect((await screen.findByRole('alert')).textContent).toBe(CATALOGUES.fr['regenerer.refused.confidential'])
    vi.mocked(lancerCompteRendu).mockRejectedValueOnce(new Error('HTTP 429: {"detail":"1 traitement(s) déjà en cours"}'))
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer ce compte rendu' }))
    await confirmerLaFenetre(userEvent)
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe(CATALOGUES.fr['regenerer.refused.busy']))
    expect(screen.getByRole('alert').textContent).not.toMatch(/quota/i)
  })
})

// ---------- Faits ----------

function monterFaits(langue: 'fr' | 'en' = 'fr') {
  return render(<LangProvider initiale={langue}><EclFaits meetingId="M1" viewScope="all" onSource={() => {}} /></LangProvider>, { wrapper: avecConfirmation })
}

describe('Faits : OpenRouter et Groq', () => {
  it.each(['fr', 'en'] as const)('prêts : proposés sous leur nom (%s), jamais « cloud privé »', async (langue) => {
    monterFaits(langue)
    expect(await screen.findByRole('option', { name: `OpenRouter · ${CATALOGUES[langue]['fournisseur.enLigne']}` })).toBeTruthy()
    expect(screen.getByRole('option', { name: `Groq · ${CATALOGUES[langue]['fournisseur.enLigne']}` })).toBeTruthy()
    expect(screen.queryByRole('option', { name: /cloud privé|private cloud/i })).toBeNull()
  })

  it('non prêts : absents', async () => {
    vi.mocked(getConfig).mockResolvedValue(NON_PRETS as never)
    monterFaits()
    expect(await screen.findByRole('option', { name: 'MLX · Local' })).toBeTruthy()
    expect(screen.queryByRole('option', { name: /OpenRouter|Groq/ })).toBeNull()
  })

  it.each(['fr', 'en'] as const)('la relance refusée par la règle des gratuits dit sa phrase (%s)', async (langue) => {
    vi.mocked(relancerFaitsReunion).mockRejectedValue(gratuit())
    monterFaits(langue)
    await screen.findByRole('option', { name: `OpenRouter · ${CATALOGUES[langue]['fournisseur.enLigne']}` })
    await userEvent.selectOptions(screen.getByRole('combobox'), 'openrouter')
    await userEvent.click(screen.getByRole('button', { name: CATALOGUES[langue]['meeting.facts.rerun'] }))
    await confirmerLaFenetre(userEvent)
    const texte = await screen.findByText(new RegExp(PHRASE[langue].slice(0, 25).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    expect(texte.textContent).toContain(PHRASE[langue])
    expect(texte.textContent).not.toMatch(/HTTP|detail/)
  })
})

// ---------- Assistant ----------

describe('Assistant', () => {
  it.each(['fr', 'en'] as const)('un refus « modèle gratuit » dit sa phrase (%s)', async (langue) => {
    vi.mocked(ask).mockRejectedValue(gratuit())
    render(<LangProvider initiale={langue}><AskPanel viewScope="p" scopeLabel="Client" /></LangProvider>, { wrapper: avecConfirmation })
    await userEvent.type(screen.getByRole('textbox'), 'Question')
    await userEvent.click(screen.getByRole('button', { name: langue === 'fr' ? 'Demander' : 'Ask' }))
    const dit = await screen.findByText(PHRASE[langue], { exact: false })
    expect(dit.textContent).not.toMatch(/HTTP|detail/)
  })

  it.each(['fr', 'en'] as const)('un quota atteint (503 à code) se dit dans la langue de l’interface (%s)', async (langue) => {
    vi.mocked(ask).mockRejectedValue(new ErreurAvecCode('step.error.rateLimited', 'Assistant indisponible : Quota atteint chez OpenRouter : attendez un moment ou choisissez un autre modèle.'))
    render(<LangProvider initiale={langue}><AskPanel viewScope="p" scopeLabel="Client" /></LangProvider>, { wrapper: avecConfirmation })
    await userEvent.type(screen.getByRole('textbox'), 'Question')
    await userEvent.click(screen.getByRole('button', { name: langue === 'fr' ? 'Demander' : 'Ask' }))
    const dit = await screen.findByText(CATALOGUES[langue]['job.step.error.rateLimited'])
    expect(dit.textContent).not.toMatch(/Assistant indisponible|HTTP|step\.error/)
  })

  it('un code inconnu dit la phrase générale de l’Assistant dans la langue de l’interface, jamais la clé brute ni la phrase du serveur', async () => {
    vi.mocked(ask).mockRejectedValue(new ErreurAvecCode('step.error.inconnu-de-l-interface', 'Assistant indisponible : phrase du serveur'))
    render(<LangProvider initiale="en"><AskPanel viewScope="p" scopeLabel="Client" /></LangProvider>, { wrapper: avecConfirmation })
    await userEvent.type(screen.getByRole('textbox'), 'Question')
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
    const dit = await screen.findByText(/The Assistant could not answer\./)
    expect(dit.textContent).not.toMatch(/step\.error|job\./)
    expect(document.body.textContent).not.toMatch(/phrase du serveur|Assistant indisponible/)
  })

  it('une panne du moteur dit la phrase générale de l’Assistant, sans « Error: » ni enveloppe HTTP ni phrase du serveur', async () => {
    // L'erreur simulée porte l'enveloppe HTTP et un corps JSON : les assertions négatives ne seraient pas vérifiables sans eux.
    vi.mocked(ask).mockRejectedValue(new Error('HTTP 503: {"detail":"Assistant indisponible : Quota atteint chez OpenRouter : attendez un moment ou choisissez un autre modèle."}'))
    render(<LangProvider initiale="fr"><AskPanel viewScope="p" scopeLabel="Client" /></LangProvider>, { wrapper: avecConfirmation })
    await userEvent.type(screen.getByRole('textbox'), 'Question')
    await userEvent.click(screen.getByRole('button', { name: 'Demander' }))
    const dit = await screen.findByText(/L’Assistant n’a pas pu répondre\./)
    expect(dit.textContent).not.toMatch(/Error|HTTP|[{}]/)
    expect(document.body.textContent).not.toMatch(/Quota atteint chez OpenRouter|Assistant indisponible/)
  })
})

// ---------- Transcrire ----------

const configTranscrire: AppConfig = {
  engines: ['whisper'], default_engine: 'whisper', default_project: 'Non classé', default_project_id: 'P1',
  whisper_model: 'w', llm_model: 'l', max_upload_mb: 2048, supported_extensions: ['.mp3'], diarization_available: true,
  llm_available: true, ...PRETS,
} as unknown as AppConfig

function choisirFichier(conteneur: HTMLElement) {
  const entree = conteneur.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(entree, { target: { files: [new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' })] } })
}

describe('Transcrire : OpenRouter et Groq', () => {
  it.each(['fr', 'en'] as const)('prêts : deux lignes sous leur nom, jamais « cloud privé » ; choisir OpenRouter règle le rapport (%s)', async (langue) => {
    const c = CATALOGUES[langue]
    const user = userEvent.setup()
    vi.mocked(submitTranscribe).mockResolvedValue('job-1')
    vi.mocked(streamJob).mockImplementation(() => vi.fn())
    const { container } = render(<LangProvider initiale={langue}><TranscribePanel config={configTranscrire} onCompleted={vi.fn()} /></LangProvider>, { wrapper: avecConfirmation })
    choisirFichier(container)
    await user.click(screen.getByRole('button', { name: c['fournisseur.enLigne'] }))
    const selecteur = screen.getByRole('group', { name: c['transcribe.mode.label'] })
    expect(within(selecteur).getByRole('radio', { name: /OpenRouter/ })).toBeTruthy()
    expect(within(selecteur).getByRole('radio', { name: /Groq/ })).toBeTruthy()
    expect(screen.queryByText(/cloud privé|private cloud/i)).toBeNull()
    await user.click(within(selecteur).getByRole('radio', { name: /OpenRouter/ }))
    await user.click(screen.getByRole('button', { name: c['transcribe.submit'] }))
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({ engine: 'whisper', llm_provider: 'openrouter' })
  })

  it.each(['fr', 'en'] as const)('un refus « modèle gratuit » dit sa phrase (%s)', async (langue) => {
    const c = CATALOGUES[langue]
    const user = userEvent.setup()
    const { container } = render(<LangProvider initiale={langue}><TranscribePanel config={configTranscrire} onCompleted={vi.fn()} /></LangProvider>, { wrapper: avecConfirmation })
    choisirFichier(container)
    await user.click(screen.getByRole('button', { name: c['fournisseur.enLigne'] }))
    const selecteur = screen.getByRole('group', { name: c['transcribe.mode.label'] })
    await user.click(within(selecteur).getByRole('radio', { name: /OpenRouter/ }))
    vi.mocked(submitTranscribe).mockRejectedValueOnce(gratuit())
    await user.click(screen.getByRole('button', { name: c['transcribe.submit'] }))
    const alerte = await screen.findByRole('alert')
    expect(alerte.textContent).toContain(PHRASE[langue])
    expect(alerte.textContent).not.toMatch(/HTTP|detail/)
  })

  it('un autre 403 garde son texte', async () => {
    const user = userEvent.setup()
    const { container } = render(<LangProvider initiale="fr"><TranscribePanel config={configTranscrire} onCompleted={vi.fn()} /></LangProvider>, { wrapper: avecConfirmation })
    choisirFichier(container)
    await user.click(screen.getByRole('button', { name: 'En ligne' }))
    const selecteur = screen.getByRole('group', { name: 'Où traiter' })
    await user.click(within(selecteur).getByRole('radio', { name: /OpenRouter/ }))
    vi.mocked(submitTranscribe).mockRejectedValueOnce(new Error('HTTP 403: {"detail":"autre"}'))
    await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
    expect((await screen.findByRole('alert')).textContent).toContain('L’envoi n’a pas pu aboutir.')
    expect(document.body.textContent).not.toMatch(/HTTP/)
  })
})

describe('Catalogues', () => {
  it.each(['fr', 'en'] as const)('les libellés des deux fournisseurs existent en %s', (langue) => {
    expect(CATALOGUES[langue]['app.provider.openrouter']).toBe('OpenRouter')
    expect(CATALOGUES[langue]['app.provider.groq']).toBe('Groq')
  })
})
