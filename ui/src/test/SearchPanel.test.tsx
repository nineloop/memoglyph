import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getTranscript, search } from '../api'
import type { SearchResult } from '../api'
import { SearchPanel } from '../components/SearchPanel'
import { LangProvider } from '../components/LangProvider'
import type { Lang } from '../lib/i18n'


vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, search: vi.fn(), getTranscript: vi.fn() }
})


const results: SearchResult[] = [
  {
    name: 'comite.md', document_id: 'doc-1', meeting_id: 'meeting-1',
    project_id: 'project-1', project: 'Client', title: 'Comité', source: 'comite.m4a',
    language: 'fr', duration: '10:00', diarization: true, profiled: false,
    n_speakers: 2, ts: '00:12', snippet: 'Première décision', rang: 1,
  },
  {
    name: 'comite-cr.md', document_id: 'doc-2', meeting_id: 'meeting-1',
    project_id: 'project-1', project: 'Client', title: 'Comité', source: 'comite.m4a',
    language: 'fr', duration: null, diarization: false, profiled: false,
    n_speakers: null, ts: '01:05', snippet: 'Deuxième passage', rang: 1,
  },
]


beforeEach(() => {
  vi.mocked(search).mockResolvedValue(results)
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'comite.md', meta: {}, body: '[00:12] Première décision', content: '',
  })
})


it('groupe les passages par identité de réunion et élargit volontairement la portée', async () => {
  const user = userEvent.setup()
  render(<SearchPanel viewScope="project-1" scopeLabel="Client" />)

  expect(screen.getByText('Portée : Projet Client')).toBeInTheDocument()
  await user.type(screen.getByRole('searchbox', { name: 'Recherche détaillée' }), 'décision')

  expect(await screen.findByText('2 passages dans 1 réunion')).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { name: 'Comité' })).toHaveLength(1)
  await user.click(screen.getByRole('button', { name: 'Chercher dans tous mes projets' }))
  await waitFor(() => expect(search).toHaveBeenLastCalledWith('décision', 'semantic', 'all'))
  expect(screen.getByText(/Toute la bibliothèque est interrogée/)).toBeInTheDocument()
})


it('ouvre un résultat horodaté dans le lecteur', async () => {
  const user = userEvent.setup()
  render(<SearchPanel viewScope="project-1" scopeLabel="Client" />)
  await user.type(screen.getByRole('searchbox', { name: 'Recherche détaillée' }), 'décision')

  await user.click(await screen.findByRole('button', { name: 'Ouvrir le passage à 00:12' }))
  await waitFor(() => expect(getTranscript).toHaveBeenCalledWith('comite.md', 'project-1'))
})


it('ignore une réponse lente de la portée précédente', async () => {
  let resolveOld!: (value: SearchResult[]) => void
  let resolveNew!: (value: SearchResult[]) => void
  vi.mocked(search)
    .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
    .mockReturnValueOnce(new Promise((resolve) => { resolveNew = resolve }))

  const { rerender } = render(
    <SearchPanel viewScope="ancien-projet" scopeLabel="Ancien" initialQuery="décision" />,
  )
  await waitFor(() => expect(search).toHaveBeenCalledWith('décision', 'semantic', 'ancien-projet'))
  rerender(<SearchPanel viewScope="nouveau-projet" scopeLabel="Nouveau" initialQuery="décision" />)
  await waitFor(() => expect(search).toHaveBeenCalledWith('décision', 'semantic', 'nouveau-projet'))

  await act(async () => resolveNew([
    { ...results[0], title: 'Résultat nouveau', project_id: 'nouveau-projet' },
  ]))
  expect(await screen.findByRole('heading', { name: 'Résultat nouveau' })).toBeInTheDocument()
  await act(async () => resolveOld([
    { ...results[0], title: 'Résultat ancien', project_id: 'ancien-projet' },
  ]))

  await waitFor(() => {
    expect(screen.queryByRole('heading', { name: 'Résultat ancien' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Résultat nouveau' })).toBeInTheDocument()
  })
})

/**
 * La hiérarchie des modes est une DÉCISION, pas un détail de rendu.
 *
 * Règle (voir `SearchPanel.tsx`) : la recherche intelligente est le
 * défaut, l'exacte reste offerte comme outil avancé.
 * Trois tests existants ont dû être modifiés pour suivre ce changement — et
 * modifier des tests pour qu'ils suivent le code est le geste le plus risqué
 * qui soit. Ceux-ci verrouillent la décision explicitement, pour qu'un retour
 * en arrière échoue au lieu de passer.
 */
describe('hiérarchie des modes de recherche', () => {
  function rendre(lang: Lang) {
    return render(
      <LangProvider initiale={lang}>
        <SearchPanel viewScope="p1" scopeLabel="Projet" />
      </LangProvider>,
    )
  }

  it('interroge le mode INTELLIGENT sans que l’utilisateur ait rien choisi', async () => {
    vi.mocked(search).mockResolvedValue([])
    rendre('fr')
    fireEvent.change(screen.getByLabelText('Recherche détaillée'), {
      target: { value: 'décision' },
    })
    await waitFor(() =>
      expect(search).toHaveBeenCalledWith('décision', 'semantic', 'p1'))
  })

  it('présente l’intelligente EN PREMIER et l’exacte comme option avancée', () => {
    vi.mocked(search).mockResolvedValue([])
    const { container } = rendre('fr')
    const boutons = Array.from(container.querySelectorAll('.seg .seg-btn'))
    expect(boutons.map((b) => b.textContent)).toEqual([
      'Recherche intelligente',
      'Texte exactAvancé',
    ])
    // Le second porte le marqueur de niveau secondaire — sans lui, les deux
    // modes se présenteraient encore comme concurrents de même rang.
    expect(boutons[0].className).not.toContain('seg-btn-secondary')
    expect(boutons[1].className).toContain('seg-btn-secondary')
  })

  it('décrit ce que la recherche intelligente FAIT vraiment : les deux à la fois', () => {
    // Ce mode n'est plus purement sémantique — il fusionne dense et
    // lexical. Une aide qui ne parlerait que de « sens » enverrait l'utilisateur
    // vers l'exact pour un acronyme, alors que l'hybride y est meilleur.
    vi.mocked(search).mockResolvedValue([])
    rendre('fr')
    const aide = screen.getByText(/Combine le sens et les mots exacts/)
    expect(aide).toBeInTheDocument()
    expect(aide.textContent).toMatch(/acronyme|nom propre/)
  })

  it('garde l’exacte accessible et honnête sur son intérêt', async () => {
    vi.mocked(search).mockResolvedValue([])
    rendre('fr')
    fireEvent.click(screen.getByText(/Texte exact/))
    // Son argument : déterministe et SANS modèle — c'est aussi le repli quand
    // l'embedding est indisponible. Le retirer ôterait un outil réel.
    expect(screen.getByText(/déterministe.*sans modèle|sans modèle.*déterministe/i))
      .toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Recherche détaillée'), {
      target: { value: 'ACME' },
    })
    await waitFor(() => expect(search).toHaveBeenCalledWith('ACME', 'fts', 'p1'))
  })

  it('traduit les deux modes en anglais', () => {
    vi.mocked(search).mockResolvedValue([])
    const { container } = rendre('en')
    const boutons = Array.from(container.querySelectorAll('.seg .seg-btn'))
    expect(boutons.map((b) => b.textContent)).toEqual([
      'Smart search',
      'Exact textAdvanced',
    ])
  })

  it('propose le texte exact quand la couche sémantique est INDISPONIBLE', async () => {
    // Trouvé à l'écran, pas par un test : la recherche intelligente étant le
    // défaut, un index à reconstruire ou un modèle absent cassent la recherche
    // par défaut. L'utilisateur voyait « Error: HTTP 503: {"detail":… } ».
    vi.mocked(search).mockRejectedValueOnce(
      new Error('HTTP 503: Index sémantique incompatible'))
    rendre('fr')
    fireEvent.change(screen.getByLabelText('Recherche détaillée'), {
      target: { value: 'décision' },
    })

    const secours = await screen.findByText('Recherche intelligente indisponible')
    expect(secours).toBeInTheDocument()
    // La cause technique reste VISIBLE : on explique, on ne masque pas.
    expect(screen.getByText(/HTTP 503/)).toBeInTheDocument()

    // Et le repli est offert, pas imposé — rien ne bascule en silence.
    expect(search).toHaveBeenCalledTimes(1)
    vi.mocked(search).mockResolvedValue([])
    fireEvent.click(screen.getByText('Chercher le texte exact à la place'))
    await waitFor(() =>
      expect(search).toHaveBeenLastCalledWith('décision', 'fts', 'p1'))
  })
})


/**
 * L'état initial de Recherche.
 */
it('dit ce que contient la portée avant la première frappe', async () => {
  render(<SearchPanel viewScope="p" scopeLabel="Client" scopeCount={12} />)
  expect(await screen.findByText(/12 documents dans Client/)).toBeInTheDocument()
  // La ligne d'aide sur le mode ne disparaît pas pour autant.
  expect(screen.getByText(/formul/i)).toBeInTheDocument()
})

it('distingue une portée VIDE d’un champ vide', async () => {
  render(<SearchPanel viewScope="p" scopeLabel="Client" scopeCount={0} />)
  expect(screen.getByText('Cette portée ne contient aucun document')).toBeInTheDocument()
  expect(screen.getByText(/Aucune recherche ne rendra de résultat dans Client/))
    .toBeInTheDocument()
})

it('se TAIT sur le compte tant que les projets ne sont pas chargés', async () => {
  render(<SearchPanel viewScope="p" scopeLabel="Client" />)
  expect(screen.queryByText(/documents dans Client/)).not.toBeInTheDocument()
  expect(screen.queryByText('Cette portée ne contient aucun document')).not.toBeInTheDocument()
})


/**
 * La bascule de portée doit gouverner AUSSI l'état initial.
 *
 * Le défaut est une COMPOSITION : les tests couvraient la bascule d'un côté,
 * le compte de l'autre, jamais les deux ensemble. Le bouton « chercher dans
 * tous mes projets » change la portée réellement interrogée sans passer par le
 * parent — l'écran continuait donc d'annoncer le projet.
 */
it('n’annonce plus une portée VIDE quand la recherche a été élargie', async () => {
  // Le cas exact : projet vide, corpus global non vide.
  const user = userEvent.setup()
  render(<SearchPanel viewScope="p" scopeLabel="Client" scopeCount={0}
                      scopeCountAll={12} scopeLabelAll="Tous mes projets" />)

  expect(screen.getByText('Cette portée ne contient aucun document')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /tous mes projets/i }))

  expect(screen.queryByText('Cette portée ne contient aucun document')).not.toBeInTheDocument()
  expect(screen.getByText(/12 documents dans Tous mes projets/)).toBeInTheDocument()
})

it('suit la bascule dans les DEUX sens', async () => {
  const user = userEvent.setup()
  render(<SearchPanel viewScope="p" scopeLabel="Client" scopeCount={3}
                      scopeCountAll={12} scopeLabelAll="Tous mes projets" />)

  expect(screen.getByText(/3 documents dans Client/)).toBeInTheDocument()
  const bouton = screen.getByRole('button', { name: /tous mes projets/i })

  await user.click(bouton)
  expect(screen.getByText(/12 documents dans Tous mes projets/)).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /Client/ }))
  expect(screen.getByText(/3 documents dans Client/)).toBeInTheDocument()
})


it('marque « généré » un passage de compte-rendu, et lui seul (audit externe, B4)', async () => {
  const user = userEvent.setup()
  vi.mocked(search).mockResolvedValue([
    { ...results[0], origine: 'transcript' },
    { ...results[1], origine: 'compte-rendu' },
  ])
  render(<SearchPanel viewScope="project-1" scopeLabel="Client" />)
  await user.type(screen.getByRole('searchbox', { name: 'Recherche détaillée' }), 'décision')

  await screen.findByText('Deuxième passage')
  const passages = screen.getAllByRole('listitem').filter((li) => li.classList.contains('search-passage'))
  expect(passages).toHaveLength(2)
  expect(passages[0]).not.toHaveTextContent('généré')
  expect(passages[1]).toHaveTextContent('généré')
})


it('sans libellé de portée du parent, la réception se dit dans la langue de l\'interface (jamais « Non classé » en dur)', () => {
  localStorage.setItem('memoglyph.lang', 'en')
  render(<LangProvider initiale="en"><SearchPanel viewScope="" /></LangProvider>)
  expect(screen.getByText(/Unclassified/)).toBeInTheDocument()
  expect(screen.queryByText(/Non classé/)).not.toBeInTheDocument()
  localStorage.setItem('memoglyph.lang', 'fr')
})
