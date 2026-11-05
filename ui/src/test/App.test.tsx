import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import type { AppConfig } from '../api'
import { createProject, getConfig, getDuplicates, getEtatAuth, getProjects, getTraitements, getTrash, search, streamJob, submitTranscribe } from '../api'
import type { ListeTraitements, Traitement } from '../api'


vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getConfig: vi.fn(),
    getProjects: vi.fn(),
    search: vi.fn(),
    getTrash: vi.fn(),
    createProject: vi.fn(),
    getEtatAuth: vi.fn(),
    getDuplicates: vi.fn(),
    getTraitements: vi.fn(),
    submitTranscribe: vi.fn(),
    streamJob: vi.fn(),
  }
})


beforeEach(() => {
  // L'application écrit dans l'URL (voir `lib/urlState.ts`). jsdom partage
  // `location` entre les tests d'un même fichier : sans cette remise à zéro,
  // un test hérite de la vue laissée par le précédent. Découvert en cassant
  // le premier test de ce bloc — ce qui prouve au passage que l'écriture
  // fonctionne.
  history.replaceState(null, '', '/')
  vi.mocked(getConfig).mockResolvedValue({
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
  } as AppConfig)
  vi.mocked(getProjects).mockResolvedValue([
    { id: '01KY243SVP5GA31F6KSBYXEAHR', project: 'Non classé', name: 'Non classé', kind: 'inbox', count: 30 },
    { id: '01KY243SVQMK79ZY6TVJW2Q3Y0', project: 'Climat', name: 'Climat', kind: 'regular', count: 1 },
  ])
  vi.mocked(search).mockResolvedValue([])
  vi.mocked(getTrash).mockResolvedValue([])
  vi.mocked(getDuplicates).mockResolvedValue([])
  vi.mocked(getTraitements).mockResolvedValue({ traitements: [], actifs: 0, en_attente: 0 })
  // Par défaut, l'état d'authentification est injoignable : l'application
  // s'affiche sans session connue, comme avant la barrière de connexion.
  vi.mocked(getEtatAuth).mockRejectedValue(new Error('réseau'))
})

/**
 * « L'application est prête » : la configuration est reçue. Ce signal était le sélecteur de
 * portée de la barre haute, retiré depuis ; l'état des services n'apparaît qu'une fois la
 * configuration là, et le panneau des projets, lui, n'existe que dans la Bibliothèque.
 */
const prete = () => screen.findByLabelText('État des services')

/** Consulter un projet : on passe par la Bibliothèque et son panneau, comme l'utilisateur. */
async function consulterProjet(user: ReturnType<typeof userEvent.setup>, nom: RegExp | string) {
  const nav = screen.queryByRole('navigation', { name: 'Projets de la bibliothèque' })
  if (!nav) await user.click(within(screen.getByRole('navigation', { name: 'Navigation principale' }))
    .getByRole('button', { name: 'Bibliothèque' }))
  const panneau = await screen.findByRole('navigation', { name: 'Projets de la bibliothèque' })
  await user.click(within(panneau).getByRole('link', { name: nom }))
}

/** Ouvre la modale « Nouveau projet » depuis le bouton du panneau de la Bibliothèque. */
async function ouvrirModaleNouveauProjet(user: ReturnType<typeof userEvent.setup>) {
  await user.click(within(screen.getByRole('navigation', { name: 'Navigation principale' }))
    .getByRole('button', { name: 'Bibliothèque' }))
  const panneau = await screen.findByRole('navigation', { name: 'Projets de la bibliothèque' })
  await user.click(within(panneau).getByRole('button', { name: 'Nouveau projet' }))
}

/** Rendu d'App avec les bouchons du bloc ci-dessus. */
function renderApp() {
  return render(<App />)
}


it('borne la portée initiale au projet système plutôt qu’à tout le corpus', async () => {
  history.replaceState(null, '', '/?vue=library')
  render(<App />)

  const panneau = await screen.findByRole('navigation', { name: 'Projets de la bibliothèque' })
  // Le consulté vient de la configuration : « Non classé », pas « Tous mes projets ».
  await waitFor(() => expect(within(panneau).getByRole('link', { name: /Non classé/ }))
    .toHaveAttribute('aria-current', 'page'))
  expect(within(panneau).getByRole('link', { name: /Tous mes projets/ })).not.toHaveAttribute('aria-current')
  expect(new URLSearchParams(location.search).get('projet')).toBe('01KY243SVP5GA31F6KSBYXEAHR')
  // Le menu natif « Portée consultée » n'existe plus.
  expect(screen.queryByRole('combobox', { name: /Projet courant/ })).toBeNull()
})


it('sans « Non classé », ouvre sur « Tous mes projets », sans erreur', async () => {
  // Une lecture ne crée pas le « Non classé » : un compte qui n'en a pas encore reçoit
  // une configuration sans projet par défaut, et une liste de projets sans lui.
  vi.mocked(getConfig).mockResolvedValue({
    engines: ['whisper'], default_engine: 'whisper',
    default_project: 'Non classé', default_project_id: '',
    whisper_model: 'whisper-test', llm_model: 'llm-test',
    max_upload_mb: 2048, supported_extensions: ['.mp3'],
    diarization_available: true, llm_available: true,
  } as AppConfig)
  vi.mocked(getProjects).mockResolvedValue([
    { id: '01KY243SVQMK79ZY6TVJW2Q3Y0', project: 'Climat', name: 'Climat', kind: 'regular', count: 1 },
  ])
  history.replaceState(null, '', '/?vue=library')
  const { container } = render(<App />)

  const panneau = await screen.findByRole('navigation', { name: 'Projets de la bibliothèque' })
  await waitFor(() => expect(within(panneau).getByRole('link', { name: /Tous mes projets/ }))
    .toHaveAttribute('aria-current', 'page'))
  // La portée est bien « tous mes projets », et non une portée vide.
  await waitFor(() => expect(new URLSearchParams(location.search).get('projet')).toBe('all'))
  expect(within(panneau).getByRole('link', { name: /Climat/ })).toBeInTheDocument()
  expect(within(panneau).queryByRole('link', { name: /Non classé/ })).toBeNull()
  expect(container.querySelector('.error.banner')).toBeNull()
})


it('l’en-tête porte le symbole Memoglyph, une variante par thème, décoratif', async () => {
  const { container } = render(<App />)
  await waitFor(() => expect(getConfig).toHaveBeenCalled())
  const marque = container.querySelector('.brand-mark')
  expect(marque).not.toBeNull()
  const sources = [...marque!.querySelectorAll('img')].map((i) => i.getAttribute('src'))
  expect(sources).toEqual(['/brand/memoglyph-symbole.svg', '/brand/memoglyph-symbole-sombre.svg'])
  // Décoratif : le nom « Memoglyph » est déjà écrit à côté.
  expect([...marque!.querySelectorAll('img')].every((i) => i.getAttribute('alt') === '')).toBe(true)
  expect(marque!.querySelector('svg.icon')).toBeNull()
})

it('l’état LLM nomme CHAQUE moteur prêt, MLX compris', async () => {
  // Ce bloc nommait deux moteurs en dur (`ollama_available`,
  // `mistral_api_key_configured`) : MLX pouvait être servi par le backend et
  // rester invisible dans l'en-tête. Une mutation a montré que rien ne le
  // vérifiait.
  vi.mocked(getConfig).mockResolvedValue({
    engines: ['whisper'], default_engine: 'whisper',
    default_project: 'Non classé', default_project_id: '01KY243SVP5GA31F6KSBYXEAHR',
    whisper_model: 'whisper-test', llm_model: 'llm-test',
    max_upload_mb: 2048, supported_extensions: ['.mp3'],
    diarization_available: true, llm_available: true,
    llm_ready: { mlx: true, mistral: true, ollama: false },
  } as AppConfig)

  render(<App />)

  const badge = await screen.findByTitle(/LLM/)
  expect(badge.getAttribute('title')).toContain('MLX')
  expect(badge.getAttribute('title')).toContain('Mistral')
  // Ollama n'est PAS prêt : l'annoncer serait aussi faux que de taire MLX.
  expect(badge.getAttribute('title')).not.toContain('Ollama')
})


it('expose les espaces de navigation dans le dock et navigue au clic', async () => {
  // La navigation primaire est un DOCK flottant :
  // ce qui compte est que TOUTES les destinations restent atteignables et que le
  // clic marque l'espace courant — pas la structure DOM d'un ancien tiroir.
  const user = userEvent.setup()
  render(<App />)

  await prete()
  const navigation = screen.getByRole('navigation', { name: 'Navigation principale' })
  for (const label of ['Transcrire', 'Bibliothèque', 'Recherche', 'Assistant', 'Voix', 'Maintenance']) {
    expect(within(navigation).getByRole('button', { name: label })).toBeInTheDocument()
  }

  await user.click(within(navigation).getByRole('button', { name: 'Assistant' }))
  await waitFor(() => expect(within(navigation).getByRole('button', { name: 'Assistant' }))
    .toHaveAttribute('aria-current', 'page'))
})


it('transmet la recherche de la barre haute à la vue Recherche, sur TOUTES les réunions', async () => {
  // Auparavant elle gardait le projet consulté ; la barre est désormais globale
  // (« toutes les réunions ») et le projet reste un bouton de l'écran.
  const user = userEvent.setup()
  render(<App />)

  const input = await screen.findByRole('searchbox', { name: 'Rechercher dans les transcripts' })
  await user.type(input, 'climat')
  await user.click(screen.getByRole('button', { name: 'Lancer la recherche' }))

  await waitFor(() => expect(search).toHaveBeenCalledWith(
    'climat',
    'semantic',
    'all',
  ))
  expect(screen.getByRole('searchbox', { name: 'Recherche détaillée' })).toHaveValue('climat')
})

/**
 * Navigation et section Données (voir `App.tsx`).
 *
 * Deux tests existants ont dû être modifiés pour suivre ces changements — le
 * texte de l'option et la liste des clés FR/EN identiques. Modifier un test
 * pour qu'il suive le code est le geste le plus risqué qui soit ; ceux-ci
 * verrouillent les décisions elles-mêmes.
 */
describe('P1 — rôle du sélecteur et découvrabilité des données', () => {
  it('plus aucun contrôle ne change de sens selon l’écran', async () => {
    // Le menu natif « Portée consultée » changeait de SENS sans changer de nom : destination dans
    // Transcrire, consultation dans la Bibliothèque, périmètre d'action dans la Corbeille. Il a
    // disparu : la destination se choisit dans Transcrire, la consultation dans le panneau.
    const { container } = renderApp()
    await prete()

    for (const ecran of [null, 'Corbeille', 'Recherche', 'Voix']) {
      if (ecran) fireEvent.click(screen.getByRole('button', { name: ecran }))
      expect(container.querySelector('.scope-kind')).toBeNull()
      expect(container.querySelector('.project-select')).toBeNull()
      expect(screen.queryByRole('combobox', { name: /Projet courant/ })).toBeNull()
    }
  })

  it('« Ranger dans » est dans Transcrire, et nulle part ailleurs', async () => {
    renderApp()
    await prete()
    fireEvent.click(screen.getByRole('button', { name: 'Transcrire' }))
    expect(await screen.findByRole('combobox', { name: 'Ranger dans' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Corbeille' }))
    expect(screen.queryByRole('combobox', { name: 'Ranger dans' })).toBeNull()
  })

  it('donne à Corbeille et Doublons leur propre entrée de navigation', async () => {
    renderApp()
    await prete()

    // Deux clics en desktop et trois en mobile avant : ils étaient enfouis
    // dans « Maintenance », dont le nom n'annonçait rien.
    for (const nom of ['Corbeille', 'Doublons', 'Maintenance']) {
      expect(screen.getByRole('button', { name: nom })).toBeInTheDocument()
    }
    expect(screen.getByText('Données')).toBeInTheDocument()
  })

  it('arrive sur la BONNE section, pas sur le diagnostic', async () => {
    renderApp()
    await prete()

    fireEvent.click(screen.getByRole('button', { name: 'Corbeille' }))
    await waitFor(() => {
      const onglet = document.querySelector('[role="tab"][aria-selected="true"]')
      expect(onglet?.textContent).toMatch(/Corbeille/)
    })
  })

  it('affiche le nombre d’éléments en corbeille, et rien quand elle est vide', async () => {
    // Le compteur rend le cycle archiver/restaurer visible sans entrer dans le
    // panneau. Sans bouchon sur `getTrash`, le `.catch` de l'application aurait
    // ramené le compteur à 0 et ce test aurait passé sans rien vérifier.
    vi.mocked(getTrash).mockResolvedValue([
      { name: 'a.md', title: 'A' }, { name: 'b.md', title: 'B' },
    ] as never)
    const { container } = renderApp()
    await waitFor(() =>
      expect(container.querySelector('.nav-badge')?.textContent).toBe('2'))
  })

  it('ne met AUCUN compteur sur Doublons', async () => {
    // Mesuré : `dedup.find_groups()` coûte 240 ms sur douze documents. Il
    // serait payé à chaque changement de portée, par tout le monde, pour un
    // chiffre que personne n'a demandé.
    vi.mocked(getTrash).mockResolvedValue([{ name: 'a.md' }] as never)
    renderApp()
    await prete()
    const doublons = screen.getByRole('button', { name: /Doublons/ })
    expect(doublons.querySelector('.nav-badge')).toBeNull()
  })

  it('ne montre PAS deux champs de recherche dans la vue Recherche', async () => {
    history.replaceState(null, '', '/?vue=transcribe')
    renderApp()
    await prete()

    // La barre haute reste l'entrée universelle depuis les autres vues…
    expect(screen.getAllByRole('searchbox')).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: 'Recherche' }))
    // …mais elle ne doit pas doubler le champ du panneau, meilleur que lui
    // (autofocus, placeholder par mode, saisie au fil de la frappe).
    await waitFor(() => {
      const champs = screen.getAllByRole('searchbox')
      expect(champs).toHaveLength(1)
      expect(champs[0]).toHaveAttribute('aria-label', 'Recherche détaillée')
    })
  })
})

/**
 * L'état de navigation dans l'URL (voir `lib/urlState.ts`).
 *
 * `urlState.test.ts` vérifie les fonctions PURES. Celui-ci vérifie le
 * CÂBLAGE : qu'App écrit réellement, relit au démarrage, et réagit au bouton
 * retour. C'est la distinction qui compte : tester une règle ne prouve pas
 * qu'elle s'applique.
 */
describe('P1 — état URL', () => {
  it('écrit la vue courante dans l’URL', async () => {
    renderApp()
    await prete()

    fireEvent.click(screen.getByRole('button', { name: 'Recherche' }))
    await waitFor(() => expect(location.search).toContain('vue=search'))

    fireEvent.click(screen.getByRole('button', { name: 'Corbeille' }))
    await waitFor(() => expect(location.search).toContain('vue=trash'))
  })

  it('relit la vue au DÉMARRAGE', async () => {
    // Sans ça, un rechargement ou un lien partagé ramènent à Transcrire.
    history.replaceState(null, '', '/?vue=voices')
    renderApp()
    await prete()
    expect(screen.getByRole('button', { name: 'Voix' }))
      .toHaveAttribute('aria-current', 'page')
  })

  it('réagit au bouton RETOUR', async () => {
    renderApp()
    await prete()

    fireEvent.click(screen.getByRole('button', { name: 'Recherche' }))
    await waitFor(() => expect(location.search).toContain('vue=search'))

    // jsdom n'implémente pas la pile d'historique : on simule le retour en
    // replaçant l'URL puis en émettant l'événement, ce que fait le navigateur.
    history.replaceState(null, '', '/?vue=library')
    fireEvent.popState(window)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Bibliothèque' }))
      .toHaveAttribute('aria-current', 'page'))
  })

  // PAS de test sur la comparaison anti-doublon d'historique — et c'est
  // délibéré.
  //
  // La garde existe dans `App.tsx` : `StrictMode` invoque les effets deux fois
  // au montage, et sans elle chaque navigation empilerait deux entrées, le
  // bouton retour paraissant coincé.
  //
  // Mais elle n'est PAS observable ici. Mesuré des deux côtés, sonde sur
  // `history.pushState`, rendu sous `StrictMode` : une seule poussée avec la
  // garde, une seule sans. Testing-Library ne reproduit pas le double appel
  // dans cette configuration.
  //
  // Trois tentatives ont produit trois tests VERTS incapables d'échouer. Les
  // garder aurait été pire que rien : ils auraient certifié une propriété que
  // personne ne vérifie. La garde reste — elle est correcte et gratuite — et
  // son absence de couverture est écrite ici plutôt que masquée.

  it('n’écrit JAMAIS la sentinelle de création de projet', async () => {
    // `__new__` ouvre la modale de création. Dans l'URL, un rechargement la
    // rouvrirait à chaque fois.
    renderApp()
    await prete()
    await ouvrirModaleNouveauProjet(userEvent.setup())
    await screen.findByRole('dialog', { name: 'Créer un projet' })
    expect(location.search).not.toContain('__new__')
  })

  it('porte la requête de recherche, et la RELANCE au retour', async () => {
    // Le piège : `SearchPanel` ne lit `initialQuery`
    // qu'au MONTAGE. C'est le changement de `key` qui le remonte. Un retour
    // arrière qui changerait l'URL sans incrémenter l'identifiant changerait
    // l'adresse sans changer l'écran.
    history.replaceState(null, '', '/?vue=search&q=climat')
    renderApp()
    await prete()
    await waitFor(() =>
      expect(vi.mocked(search)).toHaveBeenCalledWith('climat', expect.anything(), expect.anything()))

    vi.mocked(search).mockClear()
    history.replaceState(null, '', '/?vue=search&q=budget')
    fireEvent.popState(window)
    await waitFor(() =>
      expect(vi.mocked(search)).toHaveBeenCalledWith('budget', expect.anything(), expect.anything()))
  })

  /**
   * Trous nº1 à nº3 : le câblage ne possédait pas toutes les sources
   * de vérité qu'il prétendait sérialiser.
   *
   * Une nuance, mesurée ici : le piège
   * du Retour absorbe DEUX pressions puis libère, il n'enferme pas
   * indéfiniment. Au troisième passage sur `/`, `etatCourant` est identique,
   * le memo ne change pas, l'effet ne se relance pas.
   */
  const CLIMAT = '01KY243SVQMK79ZY6TVJW2Q3Y0'
  const DEFAUT = '01KY243SVP5GA31F6KSBYXEAHR'

  describe('trou nº1 — le projet de l’URL', () => {
    it('GOUVERNE la portée, plutôt que le projet par défaut', async () => {
      history.replaceState(null, '', `/?vue=library&projet=${CLIMAT}`)
      renderApp()

      const panneau = await screen.findByRole('navigation', { name: 'Projets de la bibliothèque' })
      await waitFor(() => expect(within(panneau).getByRole('link', { name: /Climat/ }))
        .toHaveAttribute('aria-current', 'page'))
    })

    it('ÉLARGIT à tous les projets et le DIT, plutôt que de replier en silence', async () => {
      // Manifeste effacé, lien périmé, ULID mal recopié.
      //
      // Un repli sur le projet par défaut ne convient pas : si le lien
      // portait aussi `reunion=R`, l'URL devenait
      // `projet=<défaut>&reunion=R` — une association invérifiable, affichée
      // comme un fait et partageable. Élargir à `all` la rend VRAIE quel que
      // soit le projet réel de la réunion, et ne perd rien du lien.
      history.replaceState(null, '', '/?vue=library&projet=01ZZZZZZZZZZZZZZZZZZZZZZZZ&reunion=01M')
      const h = pisterHistorique()
      try {
        renderApp()
        const panneau = await screen.findByRole('navigation', { name: 'Projets de la bibliothèque' })
        await waitFor(() => expect(within(panneau).getByRole('link', { name: /Tous mes projets/ }))
          .toHaveAttribute('aria-current', 'page'))

        // La réunion du lien SURVIT, sous une portée où on peut la trouver.
        await waitFor(() =>
          expect(new URLSearchParams(location.search).get('projet')).toBe('all'))
        expect(new URLSearchParams(location.search).get('reunion')).toBe('01M')

        // Le repli est une normalisation : il REMPLACE l'entrée d'arrivée.
        expect(h.pile).toHaveLength(1)

        const avis = await screen.findByRole('status')
        // « Pas accessible » couvre le projet effacé comme celui d'un autre compte, sans dire lequel.
        expect(avis).toHaveTextContent(/pas accessible depuis ce compte/)
        // Et l'avis n'est PAS habillé en panne de serveur. Réutiliser
        // l'état `error` le rendrait « Backend injoignable : … » : le message
        // s'afficherait, les tests passeraient, et l'utilisateur d'un lien
        // périmé partirait chercher une panne d'infrastructure.
        expect(avis).not.toHaveTextContent(/injoignable|unreachable/i)
        expect(document.querySelector('.error.banner')).toBeNull()
      } finally { h.restaurer() }
    })

    it('EFFACE l’avis dès qu’une portée valide est adoptée', async () => {
      // Sinon l'avertissement décrit une portée qu'on vient de quitter, et
      // reste affiché au-dessus d'un écran redevenu correct.
      history.replaceState(null, '', '/?vue=library&projet=01ZZZZZZZZZZZZZZZZZZZZZZZZ')
      renderApp()
      await prete()
      expect(await screen.findByRole('status')).toBeInTheDocument()

      await consulterProjet(userEvent.setup(), /Non classé/)
      await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
    })

    it('EFFACE l’avis au retour arrière', async () => {
      // Le retour redéfinit la portée depuis l'URL. Comme l'entrée du lien
      // périmé a été REMPLACÉE, aucune entrée d'historique ne la porte plus :
      // l'avis y serait donc toujours périmé.
      history.replaceState(null, '', '/?vue=library&projet=01ZZZZZZZZZZZZZZZZZZZZZZZZ')
      renderApp()
      await prete()
      expect(await screen.findByRole('status')).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: 'Recherche' }))
      await waitFor(() => expect(location.search).toContain('vue=search'))

      history.replaceState(null, '', `/?vue=library&projet=all`)
      fireEvent.popState(window)
      await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
    })

    it('laisse la portée VIDE tant que la configuration n’est pas reçue', async () => {
      // `viewScope === null` signale « pas encore de configuration », et
      // l'écriture d'URL en dépend. Lire l'URL ne doit pas court-circuiter ce
      // signal dès le premier rendu.
      //
      // La sonde n'est PAS le sélecteur : jsdom rabat un `<select>` sans option
      // correspondante sur sa première option. `getTrash(viewScope ?? '')` est
      // le seul témoin fidèle.
      let livrer: (config: AppConfig) => void = () => {}
      const enAttente = new Promise<AppConfig>((resoudre) => { livrer = resoudre })
      history.replaceState(null, '', `/?projet=${CLIMAT}`)
      vi.mocked(getConfig).mockReturnValue(enAttente)

      renderApp()
      await waitFor(() => expect(getTrash).toHaveBeenCalled())
      expect(vi.mocked(getTrash).mock.calls[0]).toEqual([''])

      livrer({
        engines: ['whisper'], default_engine: 'whisper',
        default_project: 'Non classé', default_project_id: DEFAUT,
        whisper_model: 'whisper-test', llm_model: 'llm-test',
        max_upload_mb: 2048, supported_extensions: ['.mp3'],
        diarization_available: true, llm_available: true,
      } as AppConfig)
      await waitFor(() => expect(getTrash).toHaveBeenCalledWith(CLIMAT))
    })
  })

  /**
   * jsdom n'a PAS de pile d'historique : `history.back()` n'y fait rien et
   * `pushState` ne laisse aucune trace observable. Un test qui compte des
   * appels n'y voit rien : le compte vaut 1 avec la garde comme sans.
   *
   * On modélise donc la pile : `pushState` ajoute, `replaceState` écrase la
   * dernière, `retour()` dépile puis émet `popstate`. La distinction
   * empiler/remplacer devient visible, et c'est ELLE qui est en cause.
   */
  function pisterHistorique() {
    const pushOriginal = history.pushState.bind(history)
    const replaceOriginal = history.replaceState.bind(history)
    const ici = () => location.pathname + location.search
    const pile: string[] = [ici()]
    const push = vi.spyOn(history, 'pushState').mockImplementation(
      (donnees, titre, url) => { pushOriginal(donnees, titre, url); pile.push(ici()) })
    const replace = vi.spyOn(history, 'replaceState').mockImplementation(
      (donnees, titre, url) => { replaceOriginal(donnees, titre, url); pile[pile.length - 1] = ici() })
    return {
      pile,
      /** Rend `false` quand la pile est au fond : le navigateur QUITTE l'application. */
      retour() {
        if (pile.length < 2) return false
        pile.pop()
        replaceOriginal(null, '', pile[pile.length - 1])
        fireEvent.popState(window)
        return true
      },
      restaurer() { push.mockRestore(); replace.mockRestore() },
    }
  }

  describe('trou nº2 — le Retour sur l’URL d’arrivée', () => {
    it('ne fait absorber AUCUN retour par la normalisation d’entrée', async () => {
      history.replaceState(null, '', '/')
      const h = pisterHistorique()
      try {
        renderApp()
        await prete()
        // `/` se normalise vers la vue REPRISE (voir `lib/vuePreferee.ts`) — ici
        // Bibliothèque, le corpus bouchonné n'étant pas vide. Ce qui est
        // testé reste la pile d'historique, pas le choix de la vue.
        await waitFor(() => expect(location.search).toContain('vue=library'))

        let absorbes = 0
        while (absorbes < 5 && h.retour()) absorbes += 1
        expect(absorbes).toBe(0)
        expect(h.pile).toHaveLength(1)
      } finally { h.restaurer() }
    })

    it('CONSOMME le drapeau même quand il n’écrit rien', async () => {
      // Le point fragile du correctif, et le seul que la mutation ait su
      // atteindre. Démarrage sur une URL DÉJÀ canonique : l'effet sort avant
      // d'écrire. Si le drapeau survivait à ce retour anticipé, la navigation
      // suivante — une vraie — écraserait l'entrée au lieu de l'empiler, et
      // l'utilisateur perdrait la vue d'où il vient.
      history.replaceState(null, '', `/?vue=search&projet=${DEFAUT}`)
      const h = pisterHistorique()
      try {
        renderApp()
        await prete()
        await waitFor(() => expect(location.search).toContain('vue=search'))
        // Rien n'a été écrit : l'URL d'arrivée était déjà la bonne.
        expect(h.pile).toHaveLength(1)

        fireEvent.click(screen.getByRole('button', { name: 'Bibliothèque' }))
        await waitFor(() => expect(location.search).toContain('vue=library'))
        expect(h.pile).toHaveLength(2)
      } finally { h.restaurer() }
    })

    it('empile TOUJOURS une entrée sur une navigation réelle', async () => {
      // Le garde-fou du correctif : remplacer au lieu d'empiler est juste pour
      // la normalisation et FAUX pour tout le reste. Sans ce test, « ne jamais
      // empiler » passerait le précédent en supprimant le bouton Retour.
      history.replaceState(null, '', '/')
      const h = pisterHistorique()
      try {
        renderApp()
        await prete()
        // Vue d'arrivée reprise, cf. le test précédent.
        await waitFor(() => expect(location.search).toContain('vue=library'))

        fireEvent.click(screen.getByRole('button', { name: 'Recherche' }))
        await waitFor(() => expect(location.search).toContain('vue=search'))
        expect(h.pile).toHaveLength(2)

        expect(h.retour()).toBe(true)
        await waitFor(() => expect(screen.getByRole('button', { name: 'Bibliothèque' }))
          .toHaveAttribute('aria-current', 'page'))
        expect(h.pile).toHaveLength(1)
      } finally { h.restaurer() }
    })
  })

  describe('trou nº3 — la saisie de la vue Recherche', () => {
    /** Amène l'application sur Recherche et rend son champ principal. */
    async function ouvrirRecherche() {
      history.replaceState(null, '', '/?vue=search')
      renderApp()
      await prete()
      // La portée arrive après la configuration : on attend que l'écriture
      // initiale soit retombée, sinon on compterait son entrée comme si la
      // frappe l'avait produite.
      await waitFor(() => expect(location.search).toContain('projet='))
      return screen.getByRole('searchbox', { name: 'Recherche détaillée' })
    }

    it('atteint l’URL', async () => {
      const user = userEvent.setup()
      const champ = await ouvrirRecherche()
      await user.type(champ, 'climat')
      await waitFor(() =>
        expect(new URLSearchParams(location.search).get('q')).toBe('climat'))
    })

    it('n’empile aucune entrée par frappe, et ne vole pas le focus', async () => {
      // Les deux fautes que le correctif pouvait introduire : remonter la
      // requête jusqu'à la `key` de `SearchPanel` le remonterait à chaque
      // caractère ; pousser une entrée par frappe rendrait Retour inutilisable.
      // L'assertion sur l'URL reste DANS ce test pour qu'un correctif absent le
      // fasse échouer aussi — un test qui ne peut pas échouer ne prouve rien.
      const user = userEvent.setup()
      const champ = await ouvrirRecherche()
      const h = pisterHistorique()
      try {
        await user.type(champ, 'climat')
        await waitFor(() =>
          expect(new URLSearchParams(location.search).get('q')).toBe('climat'))
        expect(document.activeElement).toBe(champ)
        expect(champ).toHaveValue('climat')

        await user.type(champ, ' et budget')
        await waitFor(() =>
          expect(new URLSearchParams(location.search).get('q')).toBe('climat et budget'))
        expect(document.activeElement).toBe(champ)

        // Affiner une recherche n'est pas naviguer : l'URL est REMPLACÉE.
        expect(h.pile).toHaveLength(1)
      } finally { h.restaurer() }
    })
  })
})


/**
 * La vue d'ouverture.
 *
 * Le module de décision est testé sans React dans `vuePreferee.test.ts` ; ce
 * bloc-ci vérifie le CÂBLAGE, et lui seul peut attraper le piège du lot : une
 * préférence peut être écrite parfaitement sans que sa lecture soit branchée.
 * Les deux sens sont donc exercés séparément.
 */
describe('reprendre où l’on s’était arrêté', () => {
  const CLE_VUE = 'memoglyph.vue'
  const DEFAUT_P3 = '01KY243SVP5GA31F6KSBYXEAHR'

  /** L'entrée de navigation marquée comme courante — ce que l'écran montre. */
  function vueAffichee() {
    const navigation = screen.getByRole('navigation', { name: 'Navigation principale' })
    return within(navigation).getAllByRole('button')
      .find((bouton) => bouton.getAttribute('aria-current') === 'page')
      ?.textContent?.trim()
  }

  /** Monte l'application et attend que la vue d'ouverture soit tranchée. */
  async function ouvrir() {
    renderApp()
    await prete()
    await waitFor(() => expect(location.search).toContain('vue='))
  }

  it('donne le dernier mot à l’URL — vue de travail contre préférence', async () => {
    // Le cas qui serait PIRE que le défaut corrigé : un lien reçu, écrasé par
    // la préférence locale de celui qui l'ouvre.
    localStorage.setItem(CLE_VUE, 'voices')
    history.replaceState(null, '', '/?vue=transcribe')
    await ouvrir()
    expect(vueAffichee()).toBe('Transcrire')
    expect(location.search).toContain('vue=transcribe')
  })

  it('donne le dernier mot à l’URL — l’autre sens', async () => {
    // Sans ce second sens, un câblage qui ignorerait la préférence passerait
    // le test précédent sans rien prouver.
    localStorage.setItem(CLE_VUE, 'transcribe')
    history.replaceState(null, '', '/?vue=voices')
    await ouvrir()
    expect(vueAffichee()).toBe('Voix')
    expect(location.search).toContain('vue=voices')
  })

  it('SANS vue dans l’URL, reprend la préférence mémorisée', async () => {
    // La LECTURE. Elle est branchée ou elle ne l'est pas — et si elle ne l'est
    // pas, l'écran tombe ailleurs et ce test rougit.
    localStorage.setItem(CLE_VUE, 'ask')
    history.replaceState(null, '', '/')
    await ouvrir()
    expect(vueAffichee()).toBe('Assistant')
    expect(location.search).toContain('vue=ask')
  })

  it('sans préférence, ouvre Bibliothèque quand le corpus a du contenu', async () => {
    history.replaceState(null, '', '/')
    await ouvrir()
    expect(vueAffichee()).toBe('Bibliothèque')
  })

  it('sans préférence, ouvre Transcrire quand le corpus est VIDE', async () => {
    // L'autre moitié de la règle. Le compte n'arrive qu'après le chargement des
    // projets : ce test échoue aussi si l'on tranche trop tôt, au montage.
    vi.mocked(getProjects).mockResolvedValue([
      { id: DEFAUT_P3, project: 'Non classé', name: 'Non classé', kind: 'inbox', count: 0 },
    ])
    history.replaceState(null, '', '/')
    await ouvrir()
    expect(vueAffichee()).toBe('Transcrire')
  })

  it('ne se laisse pas bloquer par une valeur de stockage inconnue', async () => {
    // Écrite par une version antérieure, ou à la main. On retombe sur la règle
    // du corpus plutôt que d'ouvrir un `<main>` vide.
    localStorage.setItem(CLE_VUE, 'chaussette')
    history.replaceState(null, '', '/')
    await ouvrir()
    expect(vueAffichee()).toBe('Bibliothèque')
  })

  it('MÉMORISE la vue de travail où l’on s’arrête', async () => {
    // L'ÉCRITURE, séparée de la lecture ci-dessus.
    history.replaceState(null, '', '/?vue=transcribe')
    await ouvrir()
    fireEvent.click(screen.getByRole('button', { name: 'Voix' }))
    await waitFor(() => expect(localStorage.getItem(CLE_VUE)).toBe('voices'))
  })

  // `dedup` est vérifié dans `vuePreferee.test.ts` : ouvrir le panneau
  // Doublons ici déclencherait un `getDuplicates` non bouchonné, dont le rejet
  // polluerait la suite entière d'une erreur non rattrapée.
  it('ne mémorise RIEN quand le corpus est inconnu', async () => {
    // Le `.catch` de `getProjects` est muet, et `.finally` marque la liste
    // « reçue » même après un échec. Trancher là-dessus reviendrait à figer
    // Transcrire comme préférence à cause d'une panne d'un instant : la reprise
    // cesserait de marcher ensuite, sans que rien ne le dise.
    vi.mocked(getProjects).mockRejectedValue(new Error('backend injoignable'))
    history.replaceState(null, '', '/')
    renderApp()
    await prete()
    await waitFor(() => expect(getProjects).toHaveBeenCalled())

    expect(vueAffichee()).toBe('Transcrire')
    expect(localStorage.getItem(CLE_VUE)).toBeNull()
  })

  it('ne mémorise PAS les détours — Corbeille, Maintenance', async () => {
    history.replaceState(null, '', '/?vue=transcribe')
    await ouvrir()
    await waitFor(() => expect(localStorage.getItem(CLE_VUE)).toBe('transcribe'))
    for (const detour of ['Corbeille', 'Maintenance']) {
      fireEvent.click(screen.getByRole('button', { name: detour }))
      await waitFor(() => expect(location.search).not.toContain('vue=transcribe'))
      // Rouvrir l'application sur la corbeille serait déroutant : on y va pour
      // une opération précise, ce n'est pas un point de départ.
      expect(localStorage.getItem(CLE_VUE)).toBe('transcribe')
    }
  })
})


/**
 * La création de projet sort des boîtes système.
 *
 * `prompt()` et `alert()` étaient hors charte, non stylables, et le second
 * BLOQUAIT le fil : refermer l'alerte d'échec faisait perdre le nom tapé.
 *
 * Le piège que ce bloc surveille n'est pas la modale elle-même — c'est le
 * `<select>` qui l'ouvre. Sa valeur passe par `__new__`, qui ne désigne aucune
 * portée et que `ecrireUrl` refuse. Deux façons de rater : le laisser affiché
 * sur « Nouveau projet… » après une annulation, ou la laisser filer dans l'URL
 * le temps d'un rendu. Les deux sont mesurées ici.
 */
describe('création de projet en modale', () => {
  const DEFAUT = '01KY243SVP5GA31F6KSBYXEAHR'
  const CLIMAT = '01KY243SVQMK79ZY6TVJW2Q3Y0'
  const CREE = '01KY243SVR0000000000000000'

  /**
   * Collecte TOUTES les URL écrites, pas seulement la dernière.
   *
   * `location.search` en fin de test ne dirait rien d'une sentinelle poussée
   * puis corrigée au rendu suivant — or c'est exactement la faute qu'on
   * craint : un rechargement pendant cette fenêtre rouvrirait la modale.
   */
  function pisterEcritures() {
    const pushOriginal = history.pushState.bind(history)
    const replaceOriginal = history.replaceState.bind(history)
    const urls: string[] = []
    const noter = (url?: string | URL | null) => { if (url != null) urls.push(String(url)) }
    const push = vi.spyOn(history, 'pushState').mockImplementation(
      (donnees, titre, url) => { noter(url); pushOriginal(donnees, titre, url) })
    const replace = vi.spyOn(history, 'replaceState').mockImplementation(
      (donnees, titre, url) => { noter(url); replaceOriginal(donnees, titre, url) })
    return { urls, restaurer() { push.mockRestore(); replace.mockRestore() } }
  }

  /** Ouvre la modale par le bouton « Nouveau projet » du panneau, et rend le dialogue. */
  async function ouvrirModale() {
    const user = userEvent.setup()
    await prete()
    await ouvrirModaleNouveauProjet(user)
    const panneau = await screen.findByRole('navigation', { name: 'Projets de la bibliothèque' })
    await waitFor(() => expect(within(panneau).getByRole('link', { name: /Non classé/ }))
      .toHaveAttribute('aria-current', 'page'))
    const dialogue = await screen.findByRole('dialog', { name: 'Créer un projet' })
    return { panneau, dialogue }
  }

  /** Le projet consulté, tel que le panneau l'indique. */
  const consulte = (panneau: HTMLElement) =>
    within(panneau).getAllByRole('link').find((l) => l.getAttribute('aria-current') === 'page')?.textContent ?? ''

  it('ouvre une modale, et n’appelle PLUS prompt()', async () => {
    const prompt = vi.spyOn(window, 'prompt')
    try {
      renderApp()
      const { dialogue } = await ouvrirModale()

      expect(dialogue).toHaveAttribute('aria-modal', 'true')
      // Le champ prend le focus : sans ça, la modale s'ouvre et il faut encore
      // aller cliquer dedans pour faire ce pour quoi on l'a ouverte.
      const champ = within(dialogue).getByLabelText('Nom du projet')
      expect(document.activeElement).toBe(champ)
      expect(within(dialogue).getByRole('button', { name: 'Fermer la création de projet' }))
        .toBeInTheDocument()

      expect(prompt).not.toHaveBeenCalled()
    } finally { prompt.mockRestore() }
  })

  it('ANNULER laisse le projet consulté inchangé, et l’URL propre', async () => {
    const user = userEvent.setup()
    history.replaceState(null, '', '/?vue=library')
    renderApp()
    // On part d'un projet NON défaut : un repli sur la première entrée passerait inaperçu
    // avec le projet par défaut.
    await prete()
    await consulterProjet(user, /Climat/)
    await waitFor(() => expect(location.search).toContain(`projet=${CLIMAT}`))
    const panneau = screen.getByRole('navigation', { name: 'Projets de la bibliothèque' })

    const e = pisterEcritures()
    try {
      await user.click(within(panneau).getByRole('button', { name: 'Nouveau projet' }))
      const dialogue = await screen.findByRole('dialog', { name: 'Créer un projet' })
      await user.click(within(dialogue).getByRole('button', { name: 'Annuler' }))

      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
      expect(consulte(panneau)).toMatch(/Climat/)
      expect(new URLSearchParams(location.search).get('projet')).toBe(CLIMAT)
      // Ni à la fin, ni entre-temps.
      expect(e.urls.filter((url) => url.includes('__new__'))).toEqual([])
    } finally { e.restaurer() }
  })

  it('ÉCHAP ferme la modale', async () => {
    renderApp()
    const { dialogue, panneau } = await ouvrirModale()
    fireEvent.keyDown(within(dialogue).getByLabelText('Nom du projet'), { key: 'Escape' })

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(consulte(panneau)).toMatch(/Non classé/)
  })

  it('refuse un nom BLANC — le bouton reste inactif et rien n’est envoyé', async () => {
    const user = userEvent.setup()
    renderApp()
    const { dialogue } = await ouvrirModale()

    const valider = within(dialogue).getByRole('button', { name: 'Créer le projet' })
    expect(valider).toBeDisabled()

    // Des espaces ne sont pas un nom. Le test tape VRAIMENT dans le champ :
    // un bouton désactivé sur `!nom` seulement passerait ici sans le `trim`.
    await user.type(within(dialogue).getByLabelText('Nom du projet'), '   ')
    expect(valider).toBeDisabled()
    await user.keyboard('{Enter}')
    expect(createProject).not.toHaveBeenCalled()

    // …et le champ redevient soumettable dès qu'il porte autre chose.
    await user.type(within(dialogue).getByLabelText('Nom du projet'), 'Climat')
    expect(valider).toBeEnabled()
  })

  it('garde l’ERREUR et la saisie DANS la modale', async () => {
    // C'est le cas qui motivait le lot : `alert()` refermé, le nom tapé était
    // perdu, et il fallait rouvrir la boîte pour tout retaper.
    const user = userEvent.setup()
    const alerte = vi.spyOn(window, 'alert').mockImplementation(() => {})
    vi.mocked(createProject).mockRejectedValue(new Error('409 nom déjà pris'))
    try {
      renderApp()
      const { dialogue, panneau } = await ouvrirModale()
      await user.type(within(dialogue).getByLabelText('Nom du projet'), 'Climat')
      await user.click(within(dialogue).getByRole('button', { name: 'Créer le projet' }))

      const erreur = await within(dialogue).findByRole('alert')
      expect(erreur).toHaveTextContent(/Création impossible.*409 nom déjà pris/)
      expect(screen.getByRole('dialog', { name: 'Créer un projet' })).toBeInTheDocument()
      expect(within(dialogue).getByLabelText('Nom du projet')).toHaveValue('Climat')
      expect(alerte).not.toHaveBeenCalled()
      // La portée n'a pas bougé, l'URL non plus.
      expect(consulte(panneau)).toMatch(/Non classé/)
      expect(location.search).not.toContain('__new__')
    } finally { alerte.mockRestore() }
  })

  it('une création RÉUSSIE ferme la modale et bascule sur le projet créé', async () => {
    const user = userEvent.setup()
    vi.mocked(createProject).mockResolvedValue(
      { id: CREE, project: 'Budget', name: 'Budget', kind: 'regular', count: 0 })
    // Le rechargement de la liste suit la création : le panneau doit y trouver le nouveau projet.
    vi.mocked(getProjects).mockResolvedValue([
      { id: DEFAUT, project: 'Non classé', name: 'Non classé', kind: 'inbox', count: 30 },
      { id: CLIMAT, project: 'Climat', name: 'Climat', kind: 'regular', count: 1 },
      { id: CREE, project: 'Budget', name: 'Budget', kind: 'regular', count: 0 },
    ])

    renderApp()
    const { dialogue, panneau } = await ouvrirModale()
    await user.type(within(dialogue).getByLabelText('Nom du projet'), '  Budget  ')
    await user.click(within(dialogue).getByRole('button', { name: 'Créer le projet' }))

    // Le nom part TRIMÉ : l'API reçoit ce que l'utilisateur voulait écrire.
    await waitFor(() => expect(createProject).toHaveBeenCalledWith('Budget'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await waitFor(() => expect(consulte(panneau)).toMatch(/Budget/))
    await waitFor(() =>
      expect(new URLSearchParams(location.search).get('projet')).toBe(CREE))
  })
})


it('un membre ne voit pas la maintenance de toute la bibliothèque', async () => {
  vi.mocked(getEtatAuth).mockResolvedValue({
    installation_requise: false, connecte: true, utilisateur: { identifiant: 'alice', admin: false },
  })
  render(<App />)

  await prete()
  const dock = screen.getByRole('navigation', { name: /principale|main/i })
  expect(within(dock).getByRole('button', { name: /Corbeille/ })).toBeInTheDocument()
  expect(within(dock).getByRole('button', { name: /Doublons/ })).toBeInTheDocument()
  expect(within(dock).queryByRole('button', { name: /Maintenance/ })).not.toBeInTheDocument()
})


it('un administrateur voit la maintenance et les projets sans propriétaire signalés', async () => {
  vi.mocked(getEtatAuth).mockResolvedValue({
    installation_requise: false, connecte: true, utilisateur: { identifiant: 'admin', admin: true },
  })
  vi.mocked(getProjects).mockResolvedValue([
    { id: '01KY243SVP5GA31F6KSBYXEAHR', project: 'Non classé', name: 'Non classé', kind: 'inbox',
      owner: 'admin', count: 3 },
    { id: '01KY243SVQMK79ZY6TVJW2Q3Y0', project: 'Ancien', name: 'Ancien', kind: 'regular',
      owner: null, count: 7 },
  ])
  render(<App />)

  await prete()
  await consulterProjet(userEvent.setup(), /Non classé/)
  const panneau = screen.getByRole('navigation', { name: 'Projets de la bibliothèque' })
  expect(await within(panneau).findByRole('link', { name: /Ancien · sans propriétaire/ })).toBeInTheDocument()
  expect(within(panneau).getByRole('link', { name: /Non classé/ })).toBeInTheDocument()
  const dock = screen.getByRole('navigation', { name: /principale|main/i })
  expect(within(dock).getByRole('button', { name: /Maintenance/ })).toBeInTheDocument()
})


/**
 * « plus aucun contrôle ne change de sens selon l'écran ».
 *
 * `RangerDans.test` ne prouve que la structure du champ. La preuve est ici, au niveau de
 * l'application : la destination (Transcrire) et la consultation (panneau) sont deux états, et
 * changer l'un ne touche JAMAIS l'autre.
 */
describe('destination et consultation sont indépendantes', () => {
  const DEFAUT = '01KY243SVP5GA31F6KSBYXEAHR'
  const CLIMAT = '01KY243SVQMK79ZY6TVJW2Q3Y0'

  async function choisirDestination(user: ReturnType<typeof userEvent.setup>, nom: string) {
    await user.click(within(screen.getByRole('navigation', { name: 'Navigation principale' }))
      .getByRole('button', { name: 'Transcrire' }))
    const champ = await screen.findByRole('combobox', { name: 'Ranger dans' })
    await user.click(champ)
    await user.click(await screen.findByRole('option', { name: nom }))
    return champ
  }

  it('changer « Ranger dans » ne change pas le projet consulté, ni l’URL', async () => {
    const user = userEvent.setup()
    history.replaceState(null, '', '/?vue=transcribe')
    renderApp()
    await prete()
    await waitFor(() => expect(new URLSearchParams(location.search).get('projet')).toBe(DEFAUT))

    const champ = await choisirDestination(user, 'Climat')
    expect(champ).toHaveValue('Climat')
    expect(new URLSearchParams(location.search).get('projet')).toBe(DEFAUT)

    await user.click(within(screen.getByRole('navigation', { name: 'Navigation principale' }))
      .getByRole('button', { name: 'Bibliothèque' }))
    const panneau = await screen.findByRole('navigation', { name: 'Projets de la bibliothèque' })
    expect(within(panneau).getByRole('link', { name: /Non classé/ })).toHaveAttribute('aria-current', 'page')
    expect(within(panneau).getByRole('link', { name: /Climat/ })).not.toHaveAttribute('aria-current')
  })

  it('consulter un projet dans le panneau ne change pas la destination', async () => {
    const user = userEvent.setup()
    history.replaceState(null, '', '/?vue=library')
    renderApp()
    await prete()
    await consulterProjet(user, /Climat/)
    await waitFor(() => expect(new URLSearchParams(location.search).get('projet')).toBe(CLIMAT))

    await user.click(within(screen.getByRole('navigation', { name: 'Navigation principale' }))
      .getByRole('button', { name: 'Transcrire' }))
    // Auparavant, la destination SUIVAIT le projet consulté.
    expect(await screen.findByRole('combobox', { name: 'Ranger dans' })).toHaveValue('Non classé')
  })

  it('mémorise le dernier projet de destination, par compte, et le propose au retour', async () => {
    const user = userEvent.setup()
    vi.mocked(getEtatAuth).mockResolvedValue({
      installation_requise: false, connecte: true, utilisateur: { identifiant: 'alice', admin: false } })
    history.replaceState(null, '', '/?vue=transcribe')
    const premier = renderApp()
    await prete()
    await choisirDestination(user, 'Climat')
    // Par compte : un autre compte du même navigateur ne reçoit pas cette destination.
    expect(localStorage.getItem('memoglyph.destination.alice')).toBe(CLIMAT)
    expect(localStorage.getItem('memoglyph.destination.bruno')).toBeNull()
    premier.unmount()

    renderApp()
    await prete()
    const champ = await screen.findByRole('combobox', { name: 'Ranger dans' })
    await waitFor(() => expect(champ).toHaveValue('Climat'))
    expect(screen.getByText(/proposé : le dernier projet utilisé/i)).toBeInTheDocument()
  })

  it('crée un projet depuis « Ranger dans » : il devient la destination, pas le projet consulté', async () => {
    const user = userEvent.setup()
    const CREE = '01KY243SVR0000000000000000'
    vi.mocked(createProject).mockResolvedValue(
      { id: CREE, project: 'Budget', name: 'Budget', kind: 'regular', count: 0 })
    history.replaceState(null, '', '/?vue=transcribe')
    renderApp()
    await prete()
    await user.click(await screen.findByRole('combobox', { name: 'Ranger dans' }))
    await user.clear(screen.getByRole('combobox', { name: 'Ranger dans' }))
    await user.type(screen.getByRole('combobox', { name: 'Ranger dans' }), 'Budget')
    vi.mocked(getProjects).mockResolvedValue([
      { id: DEFAUT, project: 'Non classé', name: 'Non classé', kind: 'inbox', count: 30 },
      { id: CLIMAT, project: 'Climat', name: 'Climat', kind: 'regular', count: 1 },
      { id: CREE, project: 'Budget', name: 'Budget', kind: 'regular', count: 0 },
    ])
    await user.click(await screen.findByRole('option', { name: /Nouveau projet.*Budget/ }))

    await waitFor(() => expect(createProject).toHaveBeenCalledWith('Budget'))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Ranger dans' })).toHaveValue('Budget'))
    expect(new URLSearchParams(location.search).get('projet')).toBe(DEFAUT)
  })
})


describe('les six autres écrans affichent la portée en lecture', () => {
  it.each(['Recherche', 'Assistant', 'Voix', 'Usage', 'Corbeille', 'Doublons'])(
    '%s : la portée se lit, sans sélecteur, avec « Changer dans la Bibliothèque »', async (ecran) => {
      const user = userEvent.setup()
      history.replaceState(null, '', '/?vue=transcribe')
      renderApp()
      await prete()
      const dock = screen.getByRole('navigation', { name: 'Navigation principale' })
      await user.click(within(dock).getByRole('button', { name: ecran }))

      const lien = await screen.findByRole('link', { name: 'Changer dans la Bibliothèque' })
      expect(lien).toHaveAttribute('href', expect.stringContaining('vue=library'))
      // Aucun sélecteur de projet : la consultation se choisit à UN seul endroit.
      expect(screen.queryByRole('combobox', { name: /projet/i })).toBeNull()

      await user.click(lien)
      await waitFor(() => expect(within(dock).getByRole('button', { name: 'Bibliothèque' }))
        .toHaveAttribute('aria-current', 'page'))
    })

  it('la recherche de la barre haute est globale : « tous mes projets »', async () => {
    const user = userEvent.setup()
    history.replaceState(null, '', '/?vue=library')
    renderApp()
    await prete()
    await consulterProjet(user, /Climat/)
    await user.type(await screen.findByRole('searchbox', { name: 'Rechercher dans les transcripts' }), 'budget')
    await user.click(screen.getByRole('button', { name: 'Lancer la recherche' }))
    // La DERNIÈRE requête est globale : un appel global suivi d'un appel limité au projet
    // (portée remise à zéro après le montage) ne doit pas passer pour un succès.
    await waitFor(() => expect(search).toHaveBeenCalled())
    await waitFor(() => expect(search).toHaveBeenLastCalledWith('budget', 'semantic', 'all'))
    expect(await screen.findByText(/Portée : Tous mes projets/)).toBeInTheDocument()
  })
})


describe('mobile : tiroir des projets et dock', () => {
  it('le bouton menu ouvre le tiroir ; choisir un projet le ferme et rend le focus au bouton', async () => {
    const user = userEvent.setup()
    history.replaceState(null, '', '/?vue=library')
    renderApp()
    await prete()
    const menu = await screen.findByRole('button', { name: 'Ouvrir les projets' })
    await user.click(menu)

    const tiroir = await screen.findByRole('dialog', { name: 'Projets de la bibliothèque' })
    await user.click(within(tiroir).getByRole('link', { name: /Climat/ }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Projets de la bibliothèque' })).toBeNull())
    expect(new URLSearchParams(location.search).get('projet')).toBe('01KY243SVQMK79ZY6TVJW2Q3Y0')
    await waitFor(() => expect(menu).toHaveFocus())
  })

  it('« Plus » ouvre les écrans secondaires : chacun est à deux touches', async () => {
    const user = userEvent.setup()
    history.replaceState(null, '', '/?vue=library')
    renderApp()
    await prete()
    const dock = screen.getByRole('navigation', { name: 'Navigation principale' })
    await user.click(within(dock).getByRole('button', { name: 'Plus' }))
    await user.click(within(screen.getByRole('group', { name: /plus d.écrans/i })).getByRole('button', { name: 'Voix' }))
    await waitFor(() => expect(location.search).toContain('vue=voices'))
  })
})


describe('le panneau des projets se souvient de son état (par compte)', () => {
  it('replié, il le reste après un rechargement ; déplié, de même', async () => {
    const user = userEvent.setup()
    vi.mocked(getEtatAuth).mockResolvedValue({
      installation_requise: false, connecte: true, utilisateur: { identifiant: 'alice', admin: false } })
    history.replaceState(null, '', '/?vue=library')
    const premier = renderApp()
    await prete()
    await user.click(await screen.findByRole('button', { name: 'Replier le panneau' }))
    expect(localStorage.getItem('memoglyph.panneau.replie.alice')).toBe('1')
    expect(localStorage.getItem('memoglyph.panneau.replie.bruno')).toBeNull()
    premier.unmount()

    history.replaceState(null, '', '/?vue=library')
    const second = renderApp()
    await prete()
    // Replié au retour : le bouton propose de le déplier.
    await user.click(await screen.findByRole('button', { name: 'Déplier le panneau' }))
    expect(localStorage.getItem('memoglyph.panneau.replie.alice')).toBe('0')
    second.unmount()

    history.replaceState(null, '', '/?vue=library')
    renderApp()
    await prete()
    expect(await screen.findByRole('button', { name: 'Replier le panneau' })).toBeInTheDocument()
  })
})



/**
 * Les traitements visibles depuis n'importe quel écran.
 */
describe('les traitements, depuis n\'importe quel écran', () => {
  const T = (extra: Partial<Traitement> = {}): Traitement => ({
    id: 'j1', nature: 'transcription', statut: 'running', en_attente: false, progression: 0.4,
    titre: 'visio du 2 novembre', projet_id: '01KY243SVQMK79ZY6TVJW2Q3Y0', projet_nom: 'Climat',
    etape: 2, etapes: 5, message_code: null, message_params: {}, debut: Date.now() / 1000 - 250,
    meeting: null, erreur: null, erreur_code: null, erreur_params: {}, ...extra,
  })
  const liste = (traitements: Traitement[], actifs: number, attente = 0): ListeTraitements =>
    ({ traitements, actifs, en_attente: attente })

  it('l\'indicateur reste visible en changeant d\'écran, jusqu\'à la fin du traitement', async () => {
    const user = userEvent.setup()
    vi.mocked(getTraitements).mockResolvedValue(liste([T()], 1))
    history.replaceState(null, '', '/?vue=library')
    renderApp()
    await prete()
    const indicateur = await screen.findByRole('button', { name: /traitements : 1 en cours, 0 en attente/i })
    const dock = screen.getByRole('navigation', { name: 'Navigation principale' })
    for (const ecran of ['Recherche', 'Assistant', 'Transcrire', 'Voix']) {
      await user.click(within(dock).getByRole('button', { name: ecran }))
      expect(screen.getByRole('button', { name: /traitements : 1 en cours/i })).toBeInTheDocument()
    }
    expect(indicateur).toBeInTheDocument()
  })

  it('n\'affiche rien quand rien ne tourne', async () => {
    renderApp()
    await prete()
    expect(screen.queryByRole('button', { name: /traitements :/i })).not.toBeInTheDocument()
  })

  it('ouvrir le panneau écrit ?traitements=1 en REMPLAÇANT l\'entrée d\'historique, jamais en empilant', async () => {
    const user = userEvent.setup()
    vi.mocked(getTraitements).mockResolvedValue(liste([T()], 1))
    history.replaceState(null, '', '/?vue=library')
    renderApp()
    await prete()
    const indicateur = await screen.findByRole('button', { name: /traitements :/i })
    await waitFor(() => expect(location.search).toContain('projet='))
    const pousse = vi.spyOn(history, 'pushState')
    try {
      await user.click(indicateur)
      expect(await screen.findByRole('dialog', { name: 'Traitements' })).toBeInTheDocument()
      await waitFor(() => expect(location.search).toContain('traitements=1'))
      expect(pousse).not.toHaveBeenCalled()
      await user.click(screen.getByRole('button', { name: 'Fermer les traitements' }))
      await waitFor(() => expect(location.search).not.toContain('traitements'))
      expect(pousse).not.toHaveBeenCalled()
    } finally { pousse.mockRestore() }
  })

  it('continuité : rouvrir l\'application sur ?traitements=1 retrouve le traitement listé', async () => {
    vi.mocked(getTraitements).mockResolvedValue(liste([T()], 1))
    history.replaceState(null, '', '/?vue=library&traitements=1')
    renderApp()
    await prete()
    const panneau = await screen.findByRole('dialog', { name: 'Traitements' })
    expect(await within(panneau).findByText('visio du 2 novembre')).toBeInTheDocument()
    expect(panneau).toHaveTextContent(/Climat · étape 2 sur 5 · depuis 4 min/)
  })

  it('« Ouvrir la réunion » d\'un traitement terminé ouvre cette réunion dans son projet et ferme le panneau', async () => {
    const user = userEvent.setup()
    vi.mocked(getTraitements).mockResolvedValue(liste([T({
      statut: 'done', progression: 1,
      meeting: { name: 'visio.md', meeting_id: '01M', project_id: '01KY243SVQMK79ZY6TVJW2Q3Y0' } })], 0))
    history.replaceState(null, '', '/?vue=transcribe&traitements=1')
    renderApp()
    await prete()
    await waitFor(() => expect(location.search).toContain('projet='))
    const pousse = vi.spyOn(history, 'pushState')
    await user.click(await screen.findByRole('link', { name: /ouvrir la réunion/i }))
    await waitFor(() => expect(location.search).toContain('vue=library'))
    // Une vraie navigation EMPILE une entrée : Retour ramène à Transcrire (jamais un remplacement).
    expect(pousse).toHaveBeenCalled()
    pousse.mockRestore()
    expect(location.search).toContain('projet=01KY243SVQMK79ZY6TVJW2Q3Y0')
    expect(location.search).not.toContain('traitements')
    expect(screen.queryByRole('dialog', { name: 'Traitements' })).not.toBeInTheDocument()
  })

  it('l\'URL du panneau n\'est jamais mémorisée comme vue d\'ouverture', async () => {
    history.replaceState(null, '', '/?vue=transcribe&traitements=1')
    renderApp()
    await prete()
    expect(localStorage.getItem('memoglyph.vue')).not.toMatch(/traitements/)
  })

  it('« Suivre » dans la confirmation d\'un envoi ouvre le panneau', async () => {
    const user = userEvent.setup()
    vi.mocked(submitTranscribe).mockResolvedValue('job-9')
    vi.mocked(streamJob).mockImplementation(() => vi.fn())
    history.replaceState(null, '', '/?vue=transcribe')
    const { container } = renderApp()
    await prete()
    const entree = await waitFor(() => {
      const el = container.querySelector<HTMLInputElement>('input[type="file"]')
      expect(el).not.toBeNull()
      return el!
    })
    await user.upload(entree, new File(['a'], 'visio.mp3', { type: 'audio/mpeg' }))
    await user.click(await screen.findByRole('button', { name: 'Lancer la transcription' }))
    const confirmation = await screen.findByRole('status', { name: /envoi accepté/i })
    expect(confirmation).toHaveTextContent(/Rangé dans/)
    // L'envoi relève la liste tout de suite, sans attendre les 3 ou 15 secondes.
    await waitFor(() => expect(vi.mocked(getTraitements).mock.calls.length).toBeGreaterThanOrEqual(2))
    await user.click(within(confirmation).getByRole('button', { name: 'Suivre' }))
    expect(await screen.findByRole('dialog', { name: 'Traitements' })).toBeInTheDocument()
  })
})


describe('la largeur de la page', () => {
  it('seules la Bibliothèque et ses réunions sortent de la largeur de 1240 px', async () => {
    const user = userEvent.setup()
    history.replaceState(null, '', '/?vue=library')
    const { container } = renderApp()
    await prete()
    const main = () => container.querySelector('main.app-main')!
    expect(main()).toHaveClass('app-main--bibliotheque')
    for (const ecran of ['Transcrire', 'Recherche', 'Assistant']) {
      await user.click(within(screen.getByRole('navigation', { name: 'Navigation principale' })).getByRole('button', { name: ecran }))
      expect(main()).not.toHaveClass('app-main--bibliotheque')
    }
    await user.click(within(screen.getByRole('navigation', { name: 'Navigation principale' })).getByRole('button', { name: 'Bibliothèque' }))
    expect(main()).toHaveClass('app-main--bibliotheque')
  })
})
