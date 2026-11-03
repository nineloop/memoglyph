import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RegenererCompteRendu } from '../components/RegenererCompteRendu'
import { modelesPrets } from '../lib/modelesPrets'
import { getLlmProvider, setLlmProvider } from '../lib/llmProvider'
import { LangProvider } from '../components/LangProvider'
import { TraitementsContext } from '../lib/traitementsContext'
import { getCloudUltra, getConfig, lancerCompteRendu, type ListeTraitements, type Traitement } from '../api'
import { CATALOGUES } from '../lib/messages'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getConfig: vi.fn(), lancerCompteRendu: vi.fn(), getCloudUltra: vi.fn() }
})

const CONFIG = {
  llm_providers: ['mistral', 'mlx', 'openai', 'anthropic', 'compatible'],
  local_providers: ['mlx'],
  default_llm_provider: 'mlx',
  llm_ready: { mlx: true, mistral: true, openai: false, anthropic: true, compatible: false },
  llm_models: { mlx: 'Qwen3-8B', mistral: 'mistral-small-latest', openai: 'gpt-x', anthropic: 'claude-sonnet-5-5', compatible: '' },
}

function traitement(sur: Partial<Traitement>): Traitement {
  return { id: 'j1', nature: 'compte-rendu', statut: 'running', en_attente: false, progression: 0.3, titre: 'Point prix',
    projet_id: 'P1', projet_nom: 'Équipe', etape: 1, etapes: 3, message_code: 'step.report.extract', message_params: {},
    debut: 0, meeting: null, erreur: null, erreur_code: null, erreur_params: {}, ...sur }
}

function monter(options: { liste?: ListeTraitements | null; rafraichir?: () => void; suivre?: () => void
  surTermine?: (n: string) => void; langue?: 'fr' | 'en'; ultra?: boolean } = {}) {
  const rafraichir = options.rafraichir ?? vi.fn()
  const suivre = options.suivre ?? vi.fn()
  const rendu = (liste: ListeTraitements | null) => (
    <LangProvider initiale={options.langue ?? 'fr'}>
      <TraitementsContext.Provider value={{ liste, rafraichir, suivre }}>
        <RegenererCompteRendu name="reunion.md" documentScope="all" surTermine={options.surTermine} ultra={options.ultra} />
      </TraitementsContext.Provider>
    </LangProvider>
  )
  const vue = render(rendu(options.liste ?? null))
  return { ...vue, rafraichir, suivre, avec: (liste: ListeTraitements | null) => vue.rerender(rendu(liste)) }
}

beforeEach(() => {
  vi.mocked(getConfig).mockReset().mockResolvedValue(CONFIG as never)
  vi.mocked(lancerCompteRendu).mockReset().mockResolvedValue({ job_id: 'j1' })
  vi.mocked(getCloudUltra).mockReset().mockResolvedValue({ autorise: false } as never)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

describe('modelesPrets', () => {
  it('liste exactement les fournisseurs proposés ET prêts, le modèle local d’abord', () => {
    expect(modelesPrets(CONFIG)).toEqual(['mlx', 'mistral', 'anthropic'])
  })
  it('un fournisseur prêt mais non proposé n’y figure jamais, ni l’alias « cloud »', () => {
    const config = { ...CONFIG, llm_providers: ['mlx', 'cloud'], llm_ready: { ...CONFIG.llm_ready, cloud: true, openai: true } }
    expect(modelesPrets(config)).toEqual(['mlx'])
  })
  it('sans aucune disponibilité déclarée : rien', () => {
    expect(modelesPrets({ llm_providers: ['mlx'], local_providers: ['mlx'] })).toEqual([])
  })
})

describe('RegenererCompteRendu', () => {
  it('montre exactement les modèles prêts, avec leur modèle configuré, et jamais un fournisseur non configuré', async () => {
    monter()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    const choix = await screen.findAllByRole('radio')
    expect(choix.map((r) => (r as HTMLInputElement).value)).toEqual(['mlx', 'mistral', 'anthropic'])
    expect(screen.getByText('Qwen3-8B')).toBeTruthy()
    expect(screen.getByText('mistral-small-latest')).toBeTruthy()
    expect(screen.getByText('claude-sonnet-5-5')).toBeTruthy()
    expect(screen.queryByText(/OpenAI/)).toBeNull()
    // Les choix sont de vrais boutons radio dans un groupe nommé.
    expect(screen.getByRole('group', { name: 'Régénérer avec le modèle' })).toBeTruthy()
  })

  it('sans fournisseur en ligne prêt : le seul modèle local, avec la marche à suivre', async () => {
    vi.mocked(getConfig).mockResolvedValue({ ...CONFIG, llm_ready: { mlx: true } } as never)
    monter()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    expect(await screen.findAllByRole('radio')).toHaveLength(1)
    expect(screen.getByText(/saisissez sa clé dans le fichier \.env/)).toBeTruthy()
  })

  it('aucun modèle prêt : une phrase, pas de bouton de lancement', async () => {
    vi.mocked(getConfig).mockResolvedValue({ ...CONFIG, llm_ready: {} } as never)
    monter()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    expect(await screen.findByText('Aucun modèle n’est prêt pour l’instant.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Régénérer ce compte rendu' })).toBeNull()
  })

  it('le lancement apparaît dans l’indicateur : la liste est rafraîchie tout de suite, avec « Suivre »', async () => {
    const { rafraichir, suivre } = monter()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    await screen.findAllByRole('radio')
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer ce compte rendu' }))
    expect(lancerCompteRendu).toHaveBeenCalledWith('reunion.md', 'all', 'mlx')
    expect(rafraichir).toHaveBeenCalledTimes(1)
    expect(await screen.findByText(/Régénération lancée avec MLX \(local\)/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Suivre' }))
    expect(suivre).toHaveBeenCalledTimes(1)
  })

  it('un fournisseur en ligne demande une confirmation avant que le texte parte', async () => {
    monter()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    await userEvent.click(await screen.findByRole('radio', { name: /Mistral/ }))
    vi.mocked(window.confirm).mockReturnValueOnce(false)
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer ce compte rendu' }))
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('Mistral (cloud)'))
    expect(lancerCompteRendu).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer ce compte rendu' }))
    expect(lancerCompteRendu).toHaveBeenCalledWith('reunion.md', 'all', 'mistral')
  })

  it('une réunion ultra-confidentielle montre les fournisseurs en ligne DÉSACTIVÉS, avec leur raison en texte', async () => {
    monter({ ultra: true })
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    const radios = await screen.findAllByRole('radio') as HTMLInputElement[]
    const parValeur = Object.fromEntries(radios.map((r) => [r.value, r]))
    expect(parValeur.mlx.disabled).toBe(false)
    expect(parValeur.mistral.disabled).toBe(true)
    expect(parValeur.anthropic.disabled).toBe(true)
    // La raison est un TEXTE visible, lié à l'option (une option désactivée ne reçoit pas le focus : pas d'infobulle seule).
    for (const p of ['mistral', 'anthropic']) {
      const raison = parValeur[p].getAttribute('aria-describedby')
      expect(raison).toBeTruthy()
      expect(document.getElementById(raison!)).toHaveTextContent('Non autorisé pour la sensibilité de ce document')
    }
    expect(parValeur.mlx.getAttribute('aria-describedby')).toBeNull()
  })

  it('en ultra, le local est présélectionné même si le fournisseur habituel est en ligne, et rien ne demande de confirmation', async () => {
    setLlmProvider('anthropic')
    monter({ ultra: true })
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    expect(((await screen.findByRole('radio', { name: /MLX/ })) as HTMLInputElement).checked).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer ce compte rendu' }))
    expect(window.confirm).not.toHaveBeenCalled()
    expect(lancerCompteRendu).toHaveBeenCalledWith('reunion.md', 'all', 'mlx')
    setLlmProvider('mlx')
  })

  it('si le compte autorise le cloud pour son ultra-confidentiel, les fournisseurs en ligne redeviennent choisissables', async () => {
    vi.mocked(getCloudUltra).mockResolvedValue({ autorise: true } as never)
    monter({ ultra: true })
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    await screen.findAllByRole('radio')
    await waitFor(() => expect((screen.getByRole('radio', { name: /Mistral/ }) as HTMLInputElement).disabled).toBe(false))
  })

  it('hors ultra, aucune option n\'est désactivée', async () => {
    monter()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    const radios = await screen.findAllByRole('radio') as HTMLInputElement[]
    expect(radios.every((r) => !r.disabled)).toBe(true)
    expect(screen.queryByText(/Non autorisé/)).toBeNull()
  })

  it('au clavier : Échap referme le panneau et rend le focus au bouton', async () => {
    monter()
    const bouton = screen.getByRole('button', { name: 'Régénérer' })
    bouton.focus()
    await userEvent.keyboard('{Enter}')
    const radio = (await screen.findAllByRole('radio'))[0]
    radio.focus()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('group', { name: 'Régénérer avec le modèle' })).toBeNull()
    expect(bouton).toHaveFocus()
    expect(bouton).toHaveAttribute('aria-expanded', 'false')
  })

  it('le choix vaut pour cette régénération seulement : le fournisseur par défaut du compte ne change pas', async () => {
    setLlmProvider('mlx')
    monter()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    await userEvent.click(await screen.findByRole('radio', { name: /Mistral/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer ce compte rendu' }))
    expect(lancerCompteRendu).toHaveBeenCalledWith('reunion.md', 'all', 'mistral')
    expect(getLlmProvider()).toBe('mlx')       // les prochains envois, le CR à la demande et l'Assistant restent locaux
  })

  it('préselectionne le fournisseur habituel s’il est prêt', async () => {
    setLlmProvider('anthropic')
    monter()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    expect((await screen.findByRole('radio', { name: /Anthropic/ }) as HTMLInputElement).checked).toBe(true)
    setLlmProvider('mlx')
  })

  it('le refus de la règle d’envoi s’affiche en une phrase, sans le texte brut du serveur', async () => {
    vi.mocked(lancerCompteRendu).mockRejectedValue(
      new Error('HTTP 403: {"detail":"Un document ultra-confidentiel ne part pas vers un fournisseur cloud"}'))
    monter()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    await userEvent.click(await screen.findByRole('radio', { name: /Anthropic/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer ce compte rendu' }))
    const alerte = await screen.findByRole('alert')
    expect(alerte.textContent).toBe('Cette réunion est ultra-confidentielle : elle ne part vers aucun fournisseur en ligne. Choisissez le modèle de cette machine.')
    expect(alerte.textContent).not.toMatch(/HTTP|detail/)
  })

  it('trop de travail : une phrase claire', async () => {
    vi.mocked(lancerCompteRendu).mockRejectedValue(new Error('HTTP 429: {"detail":"1 traitement(s) déjà en cours"}'))
    monter()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    await screen.findAllByRole('radio')
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer ce compte rendu' }))
    expect((await screen.findByRole('alert')).textContent).toBe('La machine fait déjà un autre travail : réessayez quand il sera fini.')
  })

  it('le bouton est bloqué tant que le traitement tourne, puis la nouvelle version est annoncée', async () => {
    const surTermine = vi.fn()
    const vue = monter({ surTermine })
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    await screen.findAllByRole('radio')
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer ce compte rendu' }))
    vue.avec({ traitements: [traitement({})], actifs: 1, en_attente: 0 })
    await waitFor(() => expect((screen.getByRole('button', { name: 'Régénérer ce compte rendu' }) as HTMLButtonElement).disabled).toBe(true))
    vue.avec({ traitements: [traitement({ statut: 'done', meeting: { name: 'reunion — CR (2).md' } })], actifs: 0, en_attente: 0 })
    await waitFor(() => expect(surTermine).toHaveBeenCalledWith('reunion — CR (2).md'))
    expect(await screen.findByText('Nouvelle version prête.')).toBeTruthy()
  })

  it('un traitement en échec le dit et renvoie à la vue des traitements', async () => {
    const vue = monter()
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer' }))
    await screen.findAllByRole('radio')
    await userEvent.click(screen.getByRole('button', { name: 'Régénérer ce compte rendu' }))
    vue.avec({ traitements: [traitement({ statut: 'error', erreur: 'boom' })], actifs: 0, en_attente: 0 })
    expect((await screen.findByRole('alert')).textContent).toBe('La régénération a échoué. Le détail est dans la vue des traitements.')
  })

  it('tout existe en anglais', async () => {
    monter({ langue: 'en' })
    await userEvent.click(screen.getByRole('button', { name: 'Regenerate' }))
    expect(await screen.findByRole('group', { name: 'Regenerate with the model' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Regenerate this report' })).toBeTruthy()
    expect(screen.getByRole('radio', { name: /MLX \(local\)/ })).toBeTruthy()
  })

  it('les libellés ont les mêmes clés en français et en anglais, sans répétition de texte français', () => {
    const cles = Object.keys(CATALOGUES.fr).filter((c) => c.startsWith('regenerer.'))
    expect(cles.length).toBeGreaterThanOrEqual(15)
    for (const cle of cles) {
      const fr = (CATALOGUES.fr as Record<string, string>)[cle]
      const en = (CATALOGUES.en as Record<string, string>)[cle]
      expect(en, cle).toBeTruthy()
      expect(en, cle).not.toBe(fr)
      expect(en.match(/\{[a-z]+\}/g)?.sort(), cle).toEqual(fr.match(/\{[a-z]+\}/g)?.sort())
    }
  })
})
