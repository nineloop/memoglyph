import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AppConfig } from '../api'
import { TranscribePanel } from '../components/TranscribePanel'
import { LangProvider, LangSelector } from '../components/LangProvider'
import type { Lang } from '../lib/i18n'
import { streamJob, submitTranscribe } from '../api'
import { documentBody, STICK_MARGIN_PX, isAtBottom } from '../lib/transcription'


vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    submitTranscribe: vi.fn(),
    streamJob: vi.fn(),
  }
})


const config: AppConfig = {
  engines: ['whisper'],
  default_engine: 'whisper',
  default_project: 'Non classé',
  default_project_id: '01KY243SVP5GA31F6KSBYXEAHR',
  whisper_model: 'whisper-test',
  llm_model: 'llm-test',
  max_upload_mb: 2048,
  supported_extensions: ['.mp3', '.mp4', '.wav'],
  diarization_available: true,
  llm_available: true,
}


beforeEach(() => {
  vi.mocked(submitTranscribe).mockReset()
  vi.mocked(streamJob).mockReset()
})


function selectFile(container: HTMLElement, file: File) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [file] } })
}


it('refuse immédiatement un format non supporté', () => {
  const { container } = render(
    <TranscribePanel config={config} onCompleted={vi.fn()} />,
  )

  selectFile(container, new File(['notes'], 'notes.txt', { type: 'text/plain' }))

  expect(screen.getByRole('alert')).toHaveTextContent('Format .txt non pris en charge')
  expect(screen.queryByRole('button', { name: 'Lancer la transcription' })).not.toBeInTheDocument()
})


it('affiche la destination et désactive les enrichissements indisponibles', () => {
  const { container } = render(
    <TranscribePanel
      config={{ ...config, diarization_available: false, llm_available: false }}
      projects={[{ id: 'p1', project: 'Produit', name: 'Produit', kind: 'regular', owner: 'moi', count: 1 }]}
      destinationProjectId="p1"
      onCompleted={vi.fn()}
    />,
  )
  selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

  // « Ranger dans » est visible dès l'ouverture et affiche la destination courante.
  expect(screen.getByRole('combobox', { name: /ranger dans|save to/i })).toHaveValue('Produit')
  expect(screen.getByRole('checkbox', { name: /Identifier les locuteurs/ })).toBeDisabled()
  expect(screen.getByRole('checkbox', { name: /Profilage LLM/ })).toBeDisabled()
  expect(screen.getByRole('checkbox', { name: /Compte-rendu automatique/ })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Lancer la transcription' })).toBeEnabled()
})


/**
 * LA matrice critique : MLX indisponible × Mistral configuré × mode local.
 *
 * C'est la configuration qui détruisait du travail. Les cases d'enrichissement
 * étaient initialisées sur `llm_available` — un OU sur TOUS les moteurs, donc
 * VRAI dès qu'une clé Mistral est posée. Elles arrivaient cochées, devenaient
 * désactivées faute de fournisseur local prêt, l'utilisateur ne pouvait ni les
 * décocher ni les honorer, et la requête partait avec `profile: true` vers un
 * moteur éteint. Le profilage lève alors AVANT l'écriture du Markdown : la
 * transcription était perdue.
 *
 * Deux invariants distincts, et ce test ne couvre que le premier — le second
 * (« aucun échec d'enrichissement ne perd une transcription publiable ») vit
 * dans `tests/unit/test_enrichissements_non_bloquants.py`, parce qu'il doit
 * tenir même quand l'interface est contournée par l'API.
 */
const configMlxEteint: AppConfig = {
  ...config,
  llm_available: true,          // vrai : Mistral répond
  llm_ready: { mlx: false, mistral: true },
  llm_models: { mlx: 'qwen3-4b-mlx', mistral: 'mistral-chat-test' },
  llm_providers: ['mistral', 'mlx'],
  default_llm_provider: 'mlx',
}


it('mode local avec MLX éteint : aucune case ne reste cochée ET figée', () => {
  const { container } = render(
    <TranscribePanel config={configMlxEteint} onCompleted={vi.fn()} />,
  )
  selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

  const profil = screen.getByRole('checkbox', { name: /Profilage LLM/ })
  const cr = screen.getByRole('checkbox', { name: /Compte-rendu automatique/ })

  // Le défaut exact : cochée ET désactivée. L'une ou l'autre est acceptable,
  // les deux ensemble enferment l'utilisateur dans un choix intenable.
  expect(profil).not.toBeChecked()
  expect(cr).not.toBeChecked()
})


it('mode local avec MLX éteint : la requête ne demande aucun enrichissement', async () => {
  const user = userEvent.setup()
  vi.mocked(submitTranscribe).mockResolvedValue('job-1')
  vi.mocked(streamJob).mockImplementation(() => vi.fn())

  const { container } = render(
    <TranscribePanel config={configMlxEteint} onCompleted={vi.fn()} />,
  )
  selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))
  await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))

  await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
  expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({
    llm_provider: 'mlx', profile: false, report_on: false,
  })
})


it('nomme le modèle MLX réellement servi, pas celui d’Ollama', () => {
  const { container } = render(
    <TranscribePanel config={{ ...configMlxEteint, llm_ready: { mlx: true } }}
                     onCompleted={vi.fn()} />,
  )
  selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

  // `llm_model` porte le modèle Ollama ; l'interface n'avait AUCUN nom pour
  // MLX et aurait affiché Qwen-Ollama devant un utilisateur servi par MLX.
  //
  // `getAllByText` et non `getByText` : le nom apparaît à plusieurs endroits
  // (options, panneau expert, timeline) et c'est voulu — un seul suffirait à
  // rendre le test vrai tout en laissant les autres mentir.
  expect(screen.getAllByText(/qwen3-4b-mlx/).length).toBeGreaterThan(1)
  expect(screen.queryByText(/qwen-ollama/)).not.toBeInTheDocument()
})


it('un fournisseur PÉRIMÉ dans le stockage retombe sur le défaut, et le stockage est corrigé', async () => {
  // LE risque du retrait d'Ollama : le type de fournisseur est FERMÉ
  // côté backend et refuse toute valeur inconnue (voir `api/main.py`,
  // `_fournisseur_valide`).
  // Retirer `ollama` transforme donc un choix HIER VALIDE — persisté dans le
  // `localStorage`, il survit au déploiement — en erreur dure.
  //
  // On retombe sur le défaut annoncé par l'API, et on RÉÉCRIT le stockage :
  // sans la réécriture, la correction serait à refaire à chaque chargement.
  //
  // Pas d'alias `ollama → mlx` côté backend : il ferait tourner un autre moteur
  // que celui demandé, en silence.
  localStorage.setItem('memoglyph.llmProvider', 'ollama')
  vi.mocked(submitTranscribe).mockResolvedValue('job-1')
  vi.mocked(streamJob).mockImplementation(() => vi.fn())

  const { container } = render(
    <TranscribePanel config={configMlxEteint} onCompleted={vi.fn()} />,
  )
  selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

  await waitFor(() =>
    expect(localStorage.getItem('memoglyph.llmProvider')).toBe('mlx'))

  await userEvent.setup().click(
    screen.getByRole('button', { name: 'Lancer la transcription' }))
  await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
  expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({
    llm_provider: 'mlx',
  })
})


it('un fournisseur ENCORE VALIDE n’est pas écrasé par le défaut', () => {
  // Le pendant du test précédent : la correction ne doit pas confisquer un
  // choix légitime. Mistral est au registre, il reste sélectionné.
  localStorage.setItem('memoglyph.llmProvider', 'mistral')

  const { container } = render(
    <TranscribePanel config={configMlxEteint} onCompleted={vi.fn()} />,
  )
  selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

  expect(localStorage.getItem('memoglyph.llmProvider')).toBe('mistral')
})


/**
 * Le second fournisseur cloud (OpenAI) : il ENTRE par le registre, sans jamais
 * un « == mistral » en dur, et sans transformer l'interface en tableau de bord
 * de fournisseurs. La distinction PRODUIT reste LOCAL vs CLOUD ; le choix du
 * fournisseur cloud est SECONDAIRE, visible seulement en cloud quand PLUSIEURS
 * sont prêts. Et OpenAI n'a pas de STT : le mode cloud garde whisper.
 */
const configCloudDuo: AppConfig = {
  ...config,
  llm_available: true,
  llm_ready: { mlx: true, mistral: true, openai: true },
  llm_models: {
    mlx: 'qwen3-4b-mlx',
    mistral: 'mistral-chat-test',
    openai: 'gpt-5.6-luna',
  },
  llm_providers: ['mistral', 'mlx', 'openai'],
  local_providers: ['mlx'],
  default_llm_provider: 'mlx',
}


describe('choix du fournisseur cloud (Mistral / OpenAI)', () => {
  // Le choix persiste dans le `localStorage` : sans remise à zéro, un test
  // teindrait le suivant. On ne touche QUE la clé fournisseur — `setup.ts` y a
  // posé la langue de référence, qu'un `clear()` global effacerait.
  beforeEach(() => localStorage.removeItem('memoglyph.llmProvider'))

  it('en cloud multi-fournisseur, un sélecteur SECONDAIRE Mistral / OpenAI apparaît', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <TranscribePanel config={configCloudDuo} onCompleted={vi.fn()} />,
    )
    selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

    // Le sélecteur n'existe qu'EN cloud : en local, aucun fournisseur à départager.
    expect(screen.queryByText('Fournisseur cloud')).not.toBeInTheDocument()

    await user.click(screen.getByText('Traitement cloud').closest('button')!)
    // « Mistral » apparaît AUSSI dans le détail de la tuile cloud (Voxtral +
    // Mistral Chat) : on borne la recherche au sélecteur pour ne mesurer QUE
    // les tuiles de fournisseur.
    const selecteur = screen.getByText('Fournisseur cloud').closest('.cloud-provider-select') as HTMLElement
    expect(within(selecteur).getByRole('button', { name: /Mistral/ })).toBeInTheDocument()
    expect(within(selecteur).getByRole('button', { name: /OpenAI/ })).toBeInTheDocument()
  })

  it('choisir OpenAI règle le RAPPORT sans activer un STT OpenAI : whisper reste', async () => {
    const user = userEvent.setup()
    vi.mocked(submitTranscribe).mockResolvedValue('job-1')
    vi.mocked(streamJob).mockImplementation(() => vi.fn())
    const { container } = render(
      <TranscribePanel config={configCloudDuo} onCompleted={vi.fn()} />,
    )
    selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

    await user.click(screen.getByText('Traitement cloud').closest('button')!)
    await user.click(screen.getByRole('button', { name: /OpenAI/ }))
    await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))

    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    // Le rapport part vers OpenAI ; le STT n'a PAS de variante OpenAI, il reste
    // le chemin local whisper. C'est l'invariant « pas de STT OpenAI ».
    expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({
      engine: 'whisper', llm_provider: 'openai',
    })
    expect(localStorage.getItem('memoglyph.llmProvider')).toBe('openai')
  })

  it('Anthropic est proposé comme les autres : CR et Assistant par Claude, whisper reste', async () => {
    const user = userEvent.setup()
    vi.mocked(submitTranscribe).mockResolvedValue('job-1')
    vi.mocked(streamJob).mockImplementation(() => vi.fn())
    const configTrio: AppConfig = {
      ...configCloudDuo,
      llm_ready: { ...configCloudDuo.llm_ready, anthropic: true },
      llm_models: { ...configCloudDuo.llm_models, anthropic: 'claude-haiku-5-5' },
      llm_providers: ['mistral', 'mlx', 'openai', 'anthropic'],
    }
    const { container } = render(<TranscribePanel config={configTrio} onCompleted={vi.fn()} />)
    selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

    await user.click(screen.getByText('Traitement cloud').closest('button')!)
    const selecteur = screen.getByText('Fournisseur cloud').closest('.cloud-provider-select') as HTMLElement
    const tuile = within(selecteur).getByRole('button', { name: /Anthropic \(Claude\)/ })
    expect(within(tuile).getByText('claude-haiku-5-5')).toBeInTheDocument()
    await user.click(tuile)
    await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))

    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({
      engine: 'whisper', llm_provider: 'anthropic',
    })
    // L'Assistant lit ce même choix (`getLlmProvider`).
    expect(localStorage.getItem('memoglyph.llmProvider')).toBe('anthropic')
  })

  it('clé OpenAI absente : l’enrichissement nomme CE fournisseur, pas un repli', () => {
    // Fournisseur ENREGISTRÉ mais NON prêt : il reste sélectionné (le registre le
    // connaît), et le manque se dit avec SON nom — ni « Mistral », ni « local ».
    localStorage.setItem('memoglyph.llmProvider', 'openai')
    const { container } = render(
      <TranscribePanel
        config={{ ...configCloudDuo, llm_ready: { mlx: true, mistral: true, openai: false } }}
        onCompleted={vi.fn()}
      />,
    )
    selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

    expect(
      screen.getAllByText('OpenAI — clé API absente du .env').length,
    ).toBeGreaterThan(0)
  })

  it('un seul fournisseur cloud prêt : aucun sélecteur imposé', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <TranscribePanel
        config={{ ...configCloudDuo, llm_ready: { mlx: true, mistral: true, openai: false } }}
        onCompleted={vi.fn()}
      />,
    )
    selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))
    await user.click(screen.getByText('Traitement cloud').closest('button')!)

    // Mistral seul prêt : pas de tuiles jumelles de poids égal à départager.
    expect(screen.queryByText('Fournisseur cloud')).not.toBeInTheDocument()
  })
})


describe('mode cloud retrouvé au rechargement', () => {
  // Mistral mémorisé par une session précédente : la tuile cloud s'allume au
  // chargement, sans clic. Le moteur STT doit suivre, sinon l'écran annonce
  // Voxtral pendant que l'audio part vers whisper local.
  const configVoxtral: AppConfig = {
    ...configCloudDuo,
    engines: ['whisper', 'mistral'],
  }
  beforeEach(() => localStorage.removeItem('memoglyph.llmProvider'))

  it('Mistral mémorisé : la transcription part vers Voxtral sans nouveau clic', async () => {
    const user = userEvent.setup()
    vi.mocked(submitTranscribe).mockResolvedValue('job-1')
    vi.mocked(streamJob).mockImplementation(() => vi.fn())
    localStorage.setItem('memoglyph.llmProvider', 'mistral')
    const { container } = render(
      <TranscribePanel config={configVoxtral} onCompleted={vi.fn()} />,
    )
    selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

    expect(screen.getByText('Traitement cloud').closest('button')).toHaveClass('on')
    await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))

    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({
      engine: 'mistral', llm_provider: 'mistral',
    })
  })

  it('OpenAI mémorisé : whisper reste, OpenAI n’a pas de STT', async () => {
    const user = userEvent.setup()
    vi.mocked(submitTranscribe).mockResolvedValue('job-1')
    vi.mocked(streamJob).mockImplementation(() => vi.fn())
    localStorage.setItem('memoglyph.llmProvider', 'openai')
    const { container } = render(
      <TranscribePanel config={configVoxtral} onCompleted={vi.fn()} />,
    )
    selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))
    await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))

    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({
      engine: 'whisper', llm_provider: 'openai',
    })
  })

  it('Mistral mémorisé sans moteur Voxtral déclaré : whisper, pas un moteur absent', async () => {
    const user = userEvent.setup()
    vi.mocked(submitTranscribe).mockResolvedValue('job-1')
    vi.mocked(streamJob).mockImplementation(() => vi.fn())
    localStorage.setItem('memoglyph.llmProvider', 'mistral')
    const { container } = render(
      <TranscribePanel config={configCloudDuo} onCompleted={vi.fn()} />,
    )
    selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))
    await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))

    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({ engine: 'whisper' })
  })
})


it('conserve un code libre pour les autres langues Whisper', async () => {
  const user = userEvent.setup()
  const { container } = render(<TranscribePanel config={config} onCompleted={vi.fn()} />)
  selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

  await user.selectOptions(screen.getByRole('combobox', { name: 'Langue parlée' }), '__other__')
  expect(screen.getByRole('textbox', { name: 'Code langue Whisper' })).toHaveAttribute(
    'placeholder', 'pt, nl, ar, ja…',
  )
})


it('soumet les choix puis affiche la progression et le résultat', async () => {
  const user = userEvent.setup()
  const completed = vi.fn()
  const viewMeeting = vi.fn()
  let jobHandlers: Parameters<typeof streamJob>[1] | undefined
  vi.mocked(submitTranscribe).mockResolvedValue('job-1')
  vi.mocked(streamJob).mockImplementation((_jobId, handlers) => {
    jobHandlers = handlers
    return vi.fn()
  })
  const { container } = render(
    <TranscribePanel
      config={config}
      destinationProjectId="01KY243SVP5GA31F6KSBYXEAHR"
      onCompleted={completed}
      onViewMeeting={viewMeeting}
    />,
  )
  const file = new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' })
  selectFile(container, file)
  await user.selectOptions(screen.getByRole('combobox', { name: 'Langue parlée' }), 'fr')

  await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))

  await waitFor(() => expect(streamJob).toHaveBeenCalledWith('job-1', expect.any(Object)))
  expect(submitTranscribe).toHaveBeenCalledWith(file, {
    engine: 'whisper',
    language: 'fr',
    diarize: true,
    profile: true,
    report_on: true,
    timestamps: true,
    destinationProjectId: '01KY243SVP5GA31F6KSBYXEAHR',
    // Le mode local est MLX. Cette config de test
    // n'annonce pas de `default_llm_provider` : on épingle donc le REPLI.
    llm_provider: 'mlx',
    mistralApiKey: undefined,
    // Sensibilité par défaut (config de test sans `default_sensitivity`).
    // MÉTADONNÉE : n'influence pas le reste de la charge utile.
    sensitivity: 'standard',
  })
  // Le backend rend de l'ANGLAIS et un code ; l'interface en français doit
  // afficher le français, sans quoi publier un code (voir `lib/i18n.ts`,
  // `traduireEtape`) n'a rien changé.
  act(() => jobHandlers?.onProgress?.(0.3, 'Identifying speakers…', {
    code: 'step.diarization', params: {}, message: 'Identifying speakers…',
  }))
  expect(screen.getByLabelText('Progression 30 %')).toBeInTheDocument()
  expect(screen.getByText('Diarisation (qui parle)…')).toBeInTheDocument()
  expect(screen.getByText('Transcription').closest('li')).toHaveClass('current')

  act(() => jobHandlers?.onPartial?.('Bonjour à tous.'))
  expect(screen.getByText(/Bonjour à tous/)).toBeInTheDocument()

  act(() => jobHandlers?.onDone?.({
    name: 'result.md',
    content: '---\nid: abc\n---\n# Résultat',
  }))
  expect(completed).toHaveBeenCalledOnce()
  expect(screen.getByText('result.md')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Résultat', level: 1 })).toBeInTheDocument()
  expect(screen.queryByText('id: abc')).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Télécharger' })).toHaveAttribute(
    'href',
    '/api/transcript/result.md/download?view_scope=01KY243SVP5GA31F6KSBYXEAHR',
  )
  await user.click(screen.getByRole('button', { name: 'Voir la réunion' }))
  expect(viewMeeting).toHaveBeenCalledWith('result.md', '01KY243SVP5GA31F6KSBYXEAHR')
})


it('affiche l’état de TÉLÉCHARGEMENT du modèle au premier usage', async () => {
  // La barre restait figée vers 11 % pendant le téléchargement de Whisper,
  // indiscernable d'un blocage. Le backend émet `step.model.downloading` ; la
  // ligne d'activité du panneau de traitement doit le DIRE, en français.
  const user = userEvent.setup()
  let jobHandlers: Parameters<typeof streamJob>[1] | undefined
  vi.mocked(submitTranscribe).mockResolvedValue('job-1')
  vi.mocked(streamJob).mockImplementation((_jobId, handlers) => {
    jobHandlers = handlers
    return vi.fn()
  })
  const { container } = render(
    <TranscribePanel
      config={config}
      destinationProjectId="01KY243SVP5GA31F6KSBYXEAHR"
      onCompleted={vi.fn()}
    />,
  )
  selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))
  await user.selectOptions(screen.getByRole('combobox', { name: 'Langue parlée' }), 'fr')
  await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
  await waitFor(() => expect(streamJob).toHaveBeenCalled())

  act(() => jobHandlers?.onProgress?.(
    0.11,
    'Downloading the whisper-large-v3-turbo model (first use — this can take several minutes)…',
    {
      code: 'step.model.downloading',
      params: { what: 'whisper-large-v3-turbo' },
      message: 'Downloading the whisper-large-v3-turbo model (first use — this can take several minutes)…',
    },
  ))
  expect(
    screen.getByText(/Téléchargement du modèle whisper-large-v3-turbo/),
  ).toBeInTheDocument()
  expect(screen.getByText(/premier usage/)).toBeInTheDocument()
})


it('retire uniquement le front-matter du résultat', () => {
  expect(documentBody('---\ntitle: Note\n---\n# Corps\n---\nFin')).toBe('# Corps\n---\nFin')
})


// --- Suivi automatique du texte pendant la transcription progressive ---
// Le piège : un seuil à 0 couperait le suivi dès le premier segment, car avec
// des hauteurs de ligne fractionnaires scrollTop n'atteint jamais exactement le
// bas. On teste donc la règle, pas seulement le cas idéal.

it('reste accroché quand on est pile en bas', () => {
  expect(isAtBottom({ scrollHeight: 1000, scrollTop: 800, clientHeight: 200 })).toBe(true)
})

it('reste accroché malgré un reliquat de quelques pixels', () => {
  expect(isAtBottom({ scrollHeight: 1000, scrollTop: 795, clientHeight: 200 })).toBe(true)
})

it('décroche dès que l’utilisateur remonte lire', () => {
  expect(isAtBottom({ scrollHeight: 1000, scrollTop: 400, clientHeight: 200 })).toBe(false)
})

it('décroche juste au-delà de la marge', () => {
  const top = 800 - STICK_MARGIN_PX
  expect(isAtBottom({ scrollHeight: 1000, scrollTop: top, clientHeight: 200 })).toBe(false)
})


// ---------- Les libellés doivent suivre le mode réellement choisi ----------
//
// Le panneau expert affichait le modèle Whisper et le
// modèle Ollama quel que soit le mode, et l'option « Identifier les locuteurs »
// annonçait « sans empreintes, pas de rapprochement entre réunions » — devenu
// faux depuis que le chemin cloud extrait les empreintes en local.
//
// Les mêmes contrôles TOURNENT DANS LES DEUX LANGUES. Un
// libellé qui ment ne doit pas pouvoir se cacher derrière une traduction.

const configCloud: AppConfig = {
  ...config,
  engines: ['whisper', 'mistral'],
  // Sans ça le panneau reste en mode LOCAL et les tests passent sur les
  // libellés locaux, sans rien prouver du mode cloud.
  default_engine: 'mistral',
  whisper_model: 'whisper-test',
  llm_model: 'qwen-test',
  mistral_stt_model: 'voxtral-test',
  mistral_chat_model: 'mistral-chat-test',
  llm_ready: { mistral: true, mlx: true },
  llm_providers: ['mistral', 'mlx'],
  default_llm_provider: 'mistral',
}

const LANGUES: Lang[] = ['fr', 'en']

/**
 * Ce que chaque langue DOIT dire. Les motifs « empreintes locales » et « sans
 * empreintes » se tiennent l'un l'autre : le second prouve que la phrase existe
 * bel et bien dans le catalogue, donc que le `not.toMatch` du premier n'est pas
 * un contrôle qui passe sur du vide. C'est exactement le défaut qu'avait la
 * version d'origine : elle interdisait « sans empreintes, pas de rapprochement »,
 * une formulation ABSENTE du code — l'assertion ne pouvait pas tomber.
 */
const LIBELLES: Record<Lang, {
  lancer: string
  profilage: RegExp
  locuteurs: RegExp
  langueParlee: string
  empreintesLocales: RegExp
  sansEmpreintes: RegExp
}> = {
  fr: {
    lancer: 'Lancer la transcription',
    profilage: /Profilage LLM/,
    locuteurs: /Identifier les locuteurs/,
    langueParlee: 'Langue parlée',
    empreintesLocales: /Voxtral \(cloud\)[^.]*EN LOCAL/i,
    sansEmpreintes: /sans HF_TOKEN, pas d’empreintes, donc pas de rapprochement/i,
  },
  en: {
    lancer: 'Start the transcription',
    profilage: /LLM profiling/,
    locuteurs: /Identify the speakers/,
    langueParlee: 'Spoken language',
    empreintesLocales: /Voxtral \(cloud\)[^.]*LOCALLY/i,
    sansEmpreintes: /without HF_TOKEN, no fingerprints, so no matching across meetings/i,
  },
}

/** Les options n'apparaissent qu'une fois un fichier choisi. */
function panneauCloud(lang: Lang, surcharge: Partial<AppConfig> = {}) {
  const rendu = render(
    <LangProvider initiale={lang}>
      <TranscribePanel config={{ ...configCloud, ...surcharge }} onCompleted={vi.fn()} />
    </LangProvider>,
  )
  selectFile(rendu.container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))
  return rendu
}

describe.each(LANGUES)('libellés fidèles au mode choisi (%s)', (lang) => {
  it('affiche le modèle du moteur RÉELLEMENT choisi, pas Whisper par défaut', async () => {
    panneauCloud(lang)
    await userEvent.click(document.querySelector('summary') as HTMLElement)

    expect(screen.getByText('voxtral-test')).toBeTruthy()
    expect(screen.queryByText('whisper-test')).toBeNull()
  })

  it('affiche le LLM du fournisseur choisi, pas celui d’Ollama', async () => {
    panneauCloud(lang)
    const profil = screen.getByRole('checkbox', { name: LIBELLES[lang].profilage })
    if (!(profil as HTMLInputElement).checked) await userEvent.click(profil)
    await userEvent.click(document.querySelector('summary') as HTMLElement)

    expect(screen.getByText('mistral-chat-test')).toBeTruthy()
    expect(screen.queryByText('qwen-test')).toBeNull()
  })

  it('dit que les empreintes sont calculées en LOCAL même en mode cloud', () => {
    panneauCloud(lang)

    const texte = document.body.textContent || ''
    // Formulation propre au mode CLOUD : « en local » seul passerait aussi sur
    // le libellé du mode local, et ne prouverait rien.
    expect(texte).toMatch(LIBELLES[lang].empreintesLocales)
    // L'ancienne promesse est fausse depuis que le chemin cloud extrait les
    // empreintes en local. Le test
    // jumeau ci-dessous prouve que cette phrase EXISTE encore quelque part.
    expect(texte).not.toMatch(LIBELLES[lang].sansEmpreintes)
  })

  it('avertit que sans HF_TOKEN le mode cloud ne rapproche pas les réunions', () => {
    panneauCloud(lang, { diarization_available: false })

    const texte = document.body.textContent || ''
    expect(texte).toMatch(LIBELLES[lang].sansEmpreintes)
    expect(texte).not.toMatch(LIBELLES[lang].empreintesLocales)
  })
})


// ---------- Étape F : la langue ne change RIEN de ce qui est décidé ----------

/**
 * L'état affiché, dépouillé de tout mot. On lit les cases par POSITION et non
 * par libellé : un test qui les cherche par leur nom ne pourrait pas comparer
 * deux langues, et se contenterait de vérifier qu'il a trouvé quelque chose.
 */
function decisions(container: HTMLElement) {
  return {
    modeles: [...container.querySelectorAll('.model-facts dd')].map((n) => n.textContent),
    cases: [...container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')]
      .map((c) => `coché=${c.checked} inactif=${c.disabled}`),
    tuiles: [...container.querySelectorAll('.option-tile')].map((n) => n.className),
    etapes: [...container.querySelectorAll('.job-timeline li')].map((n) => n.className),
    valeursLangue: [...container.querySelectorAll('select.option-select option')]
      .map((o) => (o as HTMLOptionElement).value),
    moteur: (container.querySelector('.expert-content select') as HTMLSelectElement | null)?.value ?? null,
  }
}

it('basculer la langue ne change aucune décision affichée', () => {
  const rendus = LANGUES.map((lang) => {
    const rendu = panneauCloud(lang)
    const etat = decisions(rendu.container)
    rendu.unmount()
    return etat
  })

  expect(rendus[1]).toEqual(rendus[0])
  // Le test doit pouvoir tomber : sans ces gardes, un composant qui n'afficherait
  // ni modèle ni case rendrait deux objets vides, égaux et vides de sens.
  expect(rendus[0].modeles).toEqual(['voxtral-test', 'mistral-chat-test'])
  expect(rendus[0].cases.length).toBeGreaterThan(0)
  expect(rendus[0].etapes.length).toBeGreaterThan(0)
})

it('basculer la langue ne change rien de ce qui part vers le backend', async () => {
  vi.mocked(submitTranscribe).mockResolvedValue('job-1')
  vi.mocked(streamJob).mockImplementation(() => vi.fn())

  const envois: unknown[] = []
  for (const lang of LANGUES) {
    const rendu = panneauCloud(lang)
    // Choisi par VALEUR, pas par libellé : la valeur envoyée est la décision.
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: LIBELLES[lang].langueParlee }), 'en',
    )
    await userEvent.click(screen.getByRole('button', { name: LIBELLES[lang].lancer }))
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    envois.push(vi.mocked(submitTranscribe).mock.calls.at(-1)?.[1])
    rendu.unmount()
  }

  expect(envois[1]).toEqual(envois[0])
  expect(envois[0]).toMatchObject({ engine: 'mistral', llm_provider: 'mistral', language: 'en' })
})


// ---------- Le déroulé nomme le modèle configuré, pas un nom gravé ----------
// L'étape « Transcription » annonçait « Whisper Large v3 Turbo » en dur : vrai
// tant que `WHISPER_MODEL` ne bouge pas, faux au premier changement. Le
// libellé doit suivre la configuration réelle.

it('nomme dans le déroulé le modèle STT réellement configuré', () => {
  const { container } = render(
    <LangProvider initiale="fr">
      <TranscribePanel
        config={{ ...config, whisper_model: 'mlx-community/whisper-small' }}
        onCompleted={vi.fn()}
      />
    </LangProvider>,
  )
  const etapeStt = [...container.querySelectorAll('.job-timeline li')][1]

  expect(etapeStt.textContent).toContain('whisper-small')
  expect(etapeStt.textContent).not.toMatch(/Large v3 Turbo/i)
})

it('suit un changement de langue EN COURS de traitement', async () => {
  // La ligne d'état était stockée déjà traduite. Changer de langue pendant un
  // traitement la laissait figée dans l'ancienne : à la fin, un « Terminé »
  // d'une langue sous un titre de l'autre. On garde le code, on traduit au
  // rendu — ce test échoue si on revient à un `string` en état.
  let jobHandlers: Parameters<typeof streamJob>[1] | undefined
  vi.mocked(submitTranscribe).mockResolvedValue('job-1')
  vi.mocked(streamJob).mockImplementation((_id, handlers) => {
    jobHandlers = handlers
    return () => {}
  })

  const rendu = render(
    <LangProvider initiale="fr">
      <LangSelector />
      <TranscribePanel config={configCloud} onCompleted={vi.fn()} />
    </LangProvider>,
  )
  selectFile(rendu.container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))
  fireEvent.click(screen.getByRole('button', { name: /Lancer|Transcrire/i }))
  await waitFor(() => expect(jobHandlers).toBeDefined())

  act(() => jobHandlers?.onProgress?.(0.5, 'Profiling with qwen…', {
    code: 'step.profiling', params: { model: 'qwen' }, message: 'Profiling with qwen…',
  }))
  expect(screen.getByText('Profilage avec qwen…')).toBeInTheDocument()

  fireEvent.change(screen.getByLabelText('Langue de l’interface'), { target: { value: 'en' } })
  expect(screen.getByText('Profiling with qwen…')).toBeInTheDocument()
  expect(screen.queryByText('Profilage avec qwen…')).not.toBeInTheDocument()
})


describe('sensibilité du document (métadonnée)', () => {
  const configSensible: AppConfig = {
    ...config,
    sensitivity_levels: ['ultra_confidential', 'confidential', 'standard'],
    default_sensitivity: 'standard',
  }

  it('propose les 3 niveaux venus du backend et défaut sur `standard`', () => {
    const { container } = render(
      <TranscribePanel config={configSensible} onCompleted={vi.fn()} />,
    )
    selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

    const select = screen.getByRole('combobox', { name: 'Sensibilité du document' }) as HTMLSelectElement
    expect([...select.options].map((o) => o.value)).toEqual([
      'ultra_confidential', 'confidential', 'standard',
    ])
    expect(select.value).toBe('standard') // défaut annoncé par le backend
  })

  it('envoie le niveau CHOISI à la soumission', async () => {
    const user = userEvent.setup()
    vi.mocked(submitTranscribe).mockResolvedValue('job-1')
    vi.mocked(streamJob).mockImplementation(() => vi.fn())
    const { container } = render(
      <TranscribePanel config={configSensible} onCompleted={vi.fn()} />,
    )
    selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Sensibilité du document' }),
      'ultra_confidential',
    )
    await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))

    await waitFor(() => expect(submitTranscribe).toHaveBeenCalled())
    expect(vi.mocked(submitTranscribe).mock.calls[0][1]).toMatchObject({
      sensitivity: 'ultra_confidential',
    })
  })

  it('sans vocabulaire backend, retombe sur un seul niveau `standard`', () => {
    // Compat : un backend antérieur n'envoie pas `sensitivity_levels`.
    const { container } = render(
      <TranscribePanel config={config} onCompleted={vi.fn()} />,
    )
    selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))
    const select = screen.getByRole('combobox', { name: 'Sensibilité du document' }) as HTMLSelectElement
    expect([...select.options].map((o) => o.value)).toEqual(['standard'])
  })
})


describe('encart du compagnon de capture', () => {
  it.each([
    ['fr', 'Enregistrer une visio en direct', /mettez un casque/, /« Appareils »/],
    ['en', 'Record a live video call', /wear a headset/, /“Devices”/],
  ] as const)('explique le compagnon en %s, sans le piloter', (lang, titre, casque, appareils) => {
    render(
      <LangProvider initiale={lang}>
        <TranscribePanel config={config} onCompleted={vi.fn()} />
      </LangProvider>,
    )
    const encart = screen.getByRole('region', { name: titre })
    expect(encart).toHaveTextContent(casque)
    expect(encart).toHaveTextContent('compagnon/README.md')
    expect(encart).toHaveTextContent(appareils)
    expect(within(encart).queryByRole('button')).not.toBeInTheDocument()
    expect(within(encart).queryByRole('link')).not.toBeInTheDocument()
  })
})


// ---------- Chantier 003, tranche B : la confirmation « Rangé dans… » et « Suivre » (T044) ----------

describe('confirmation d\'un envoi accepté (SC-003)', () => {
  const projets = [
    { id: 'inbox', project: 'Non classé', name: 'Non classé', kind: 'inbox' as const, owner: 'moi', count: 0 },
    { id: 'p1', project: 'Réunions AMI', name: 'Réunions AMI', kind: 'regular' as const, owner: 'moi', count: 2 },
  ]

  async function lancer(props: Partial<React.ComponentProps<typeof TranscribePanel>> = {}) {
    const user = userEvent.setup()
    vi.mocked(submitTranscribe).mockResolvedValue('job-7')
    vi.mocked(streamJob).mockImplementation(() => vi.fn())
    const { container } = render(
      <TranscribePanel config={config} projects={projets} destinationProjectId="p1"
                       onCompleted={vi.fn()} {...props} />)
    selectFile(container, new File(['audio'], 'visio.mp3', { type: 'audio/mpeg' }))
    await user.click(screen.getByRole('button', { name: /lancer la transcription|start the transcription/i }))
    return user
  }

  it('dit où la réunion est rangée et propose de la suivre', async () => {
    const onSuivre = vi.fn()
    const onSubmitted = vi.fn()
    const user = await lancer({ onSuivre, onSubmitted })
    const confirmation = await screen.findByRole('status', { name: /envoi accepté/i })
    expect(confirmation).toHaveTextContent('Rangé dans Réunions AMI.')
    expect(onSubmitted).toHaveBeenCalledWith('job-7')
    await user.click(within(confirmation).getByRole('button', { name: 'Suivre' }))
    expect(onSuivre).toHaveBeenCalledTimes(1)
  })

  it('traduit la réception du système : « Saved to Unclassified » en anglais', async () => {
    localStorage.setItem('memoglyph.lang', 'en')
    await lancer({ destinationProjectId: 'inbox' })
    expect(await screen.findByRole('status', { name: /upload accepted/i })).toHaveTextContent('Saved to Unclassified.')
  })

  it('le formulaire reste PRÊT pour un autre envoi dès que le premier est accepté (spec US5, scénario 6)', async () => {
    const arret1 = vi.fn()
    vi.mocked(submitTranscribe).mockResolvedValueOnce('job-1').mockResolvedValueOnce('job-2')
    vi.mocked(streamJob).mockReset()
    vi.mocked(streamJob).mockReturnValueOnce(arret1).mockReturnValue(vi.fn())
    const user = userEvent.setup()
    const { container } = render(
      <TranscribePanel config={config} projects={projets} destinationProjectId="p1" onCompleted={vi.fn()} />)
    selectFile(container, new File(['a'], 'premier.mp3', { type: 'audio/mpeg' }))
    await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
    await screen.findByRole('status', { name: /envoi accepté/i })

    // Le premier est accepté : rien n'est verrouillé, on peut déposer et lancer un autre fichier, et le
    // bouton LE DIT (il n'affiche plus « Traitement en cours… » grisé).
    expect(screen.getByRole('combobox', { name: /ranger dans/i })).toBeEnabled()
    expect(screen.queryByText('Traitement en cours…')).not.toBeInTheDocument()
    const lancer = screen.getByRole('button', { name: 'Transcrire un autre enregistrement' })
    expect(lancer).toBeEnabled()
    selectFile(container, new File(['b'], 'second.mp3', { type: 'audio/mpeg' }))
    await user.click(screen.getByRole('button', { name: 'Transcrire un autre enregistrement' }))
    await waitFor(() => expect(submitTranscribe).toHaveBeenCalledTimes(2))
    // Le premier flux n'est plus suivi ici (il reste visible dans le panneau des traitements).
    expect(arret1).toHaveBeenCalled()
    await waitFor(() => expect(streamJob).toHaveBeenLastCalledWith('job-2', expect.any(Object)))
  })

  it('pendant l\'envoi lui-même (avant l\'acceptation), le formulaire est verrouillé', async () => {
    let accepter: (id: string) => void = () => {}
    vi.mocked(submitTranscribe).mockReturnValue(new Promise<string>((r) => { accepter = r }))
    vi.mocked(streamJob).mockImplementation(() => vi.fn())
    const user = userEvent.setup()
    const { container } = render(
      <TranscribePanel config={config} projects={projets} destinationProjectId="p1" onCompleted={vi.fn()} />)
    selectFile(container, new File(['a'], 'a.mp3', { type: 'audio/mpeg' }))
    await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
    expect(screen.getByRole('combobox', { name: /ranger dans/i })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Lancer la transcription' })).not.toBeInTheDocument()
    act(() => accepter('job-3'))
    await screen.findByRole('status', { name: /envoi accepté/i })
    expect(screen.getByRole('combobox', { name: /ranger dans/i })).toBeEnabled()
  })

  it('dit « en attente » quand le créneau est occupé', async () => {
    const traitements = { actifs: 0, en_attente: 1, traitements: [{
      id: 'job-7', nature: 'transcription', statut: 'running' as const, en_attente: true, progression: 0,
      titre: 'visio', projet_id: 'p1', projet_nom: 'Réunions AMI', etape: null, etapes: 3,
      message_code: null, message_params: {}, debut: 1, meeting: null,
      erreur: null, erreur_code: null, erreur_params: {} }] }
    await lancer({ traitements })
    expect(await screen.findByRole('status', { name: /envoi accepté/i }))
      .toHaveTextContent(/un autre traitement est en cours/i)
  })
})


it('l’échec d’un traitement se dit par son CODE traduit, jamais par le texte brut du serveur', async () => {
  const user = userEvent.setup()
  vi.mocked(submitTranscribe).mockResolvedValue('job-9')
  let gestionnaires: { onError?: (err: string, etape?: { code: string | null; params: Record<string, string | number>; message: string }) => void } = {}
  vi.mocked(streamJob).mockImplementation(((_id: string, h: typeof gestionnaires) => { gestionnaires = h; return vi.fn() }) as never)

  const { container } = render(<TranscribePanel config={configMlxEteint} onCompleted={vi.fn()} />)
  selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))
  await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
  await waitFor(() => expect(gestionnaires.onError).toBeDefined())

  act(() => {
    gestionnaires.onError?.('Extraction vide : aucune unité retenue sur 3 appel(s) au modèle, dont 3 refusé(s).',
      { code: 'step.error.emptyExtraction', params: {}, message: 'brut' })
  })

  expect(await screen.findByText('Le modèle n’a rien retenu de cette réunion. Essayez avec un autre modèle.')).toBeInTheDocument()
  expect(screen.queryByText(/Extraction vide/)).not.toBeInTheDocument()
})

it('sans code (serveur plus ancien), le texte du serveur reste affiché', async () => {
  const user = userEvent.setup()
  vi.mocked(submitTranscribe).mockResolvedValue('job-9')
  let gestionnaires: { onError?: (err: string) => void } = {}
  vi.mocked(streamJob).mockImplementation(((_id: string, h: typeof gestionnaires) => { gestionnaires = h; return vi.fn() }) as never)

  const { container } = render(<TranscribePanel config={configMlxEteint} onCompleted={vi.fn()} />)
  selectFile(container, new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' }))
  await user.click(screen.getByRole('button', { name: 'Lancer la transcription' }))
  await waitFor(() => expect(gestionnaires.onError).toBeDefined())
  act(() => { gestionnaires.onError?.('Panne inconnue du serveur') })

  expect(await screen.findByText('Panne inconnue du serveur')).toBeInTheDocument()
})
