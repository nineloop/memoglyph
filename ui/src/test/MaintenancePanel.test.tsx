import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import {
  getDuplicates, getMaintenanceStatus, getTrash, reindexMaintenance, startBackfill,
  startFaithfulBackfill, streamJob,
} from '../api'
import { LangProvider, LangSelector } from '../components/LangProvider'
import { MaintenancePanel } from '../components/MaintenancePanel'


vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getMaintenanceStatus: vi.fn(),
    reindexMaintenance: vi.fn(),
    startBackfill: vi.fn(),
    startFaithfulBackfill: vi.fn(),
    streamJob: vi.fn(),
    getDuplicates: vi.fn(),
    getTrash: vi.fn(),
  }
})


beforeEach(() => {
  vi.mocked(getMaintenanceStatus).mockResolvedValue({
    documents: 4, diarized_documents: 3, semantic_documents: 3,
    semantic_chunks: 12, voice_documents: 2, voice_occurrences: 5,
    backfill_eligible: ['ancien.md'],
    faithful_backfill_eligible: ['archive.md'],
    faithful_backfill_missing_originals: ['sans-original.md'], duplicate_groups: 1,
  })
  vi.mocked(reindexMaintenance).mockResolvedValue({ indexed: 2 })
  vi.mocked(startBackfill).mockResolvedValue({ job_id: 'job-1' })
  vi.mocked(startFaithfulBackfill).mockResolvedValue({ job_id: 'job-2' })
  vi.mocked(streamJob).mockImplementation((_jobId, handlers) => {
    handlers.onDone?.({ name: 'done', content: '' })
    return () => {}
  })
  vi.mocked(getDuplicates).mockResolvedValue([])
  vi.mocked(getTrash).mockResolvedValue([])
})


/**
 * « Dans : … » N'EST PAS un point de synchronisation.
 *
 * Ce libellé est rendu inconditionnellement (`MaintenancePanel.tsx:154`), hors
 * du garde `loading || !status` : il est là dès le PREMIER rendu, avant que
 * `getMaintenanceStatus` n'ait répondu. S'y attendre puis interroger `status`
 * en SYNCHRONE, c'est attendre autre chose que ce dont on a besoin.
 *
 * Le test passait presque toujours parce que `mockResolvedValue` rend une
 * promesse DÉJÀ résolue : la mise à jour d'état arrive avant le premier sondage
 * de `findByText`. Presque — un échec sur ~9 exécutions du portail complet,
 * en 264 ms, donc SANS aucun délai dépassé.
 *
 * Reproduit en ralentissant l'API de 30 ms, ce qu'un vrai appel HTTP est :
 * échec en 24 ms sur « 3/4 », le DOM affichant « Lecture des diagnostics… ».
 *
 * On attend donc une donnée qui EXIGE le statut. Les assertions synchrones qui
 * suivent sont alors garanties, et le restent.
 */
async function attendreLesDiagnostics() {
  expect(await screen.findByText('Dans : Projet Client')).toBeInTheDocument()
  return screen.findByText('3/4')
}


it('borne diagnostics et réindexations au projet courant', async () => {
  const user = userEvent.setup()
  render(<MaintenancePanel viewScope="project-1" scopeLabel="Client" />)

  expect(await attendreLesDiagnostics()).toBeInTheDocument()
  expect(screen.getByText(/1 document hors de l’index de recherche/)).toBeInTheDocument()
  expect(getMaintenanceStatus).toHaveBeenCalledWith('project-1')

  await user.click(screen.getByRole('button', { name: 'Rattacher' }))
  await waitFor(() => expect(reindexMaintenance).toHaveBeenCalledWith('voices', 'project-1'))
})


it('regroupe backfill et doublons sans perdre la portée', async () => {
  const user = userEvent.setup()
  render(<MaintenancePanel viewScope="project-1" scopeLabel="Client" />)
  // Même raison qu'au-dessus : l'onglet Backfill affiche « Analyse des médias
  // disponibles… » tant que `status` est nul (`MaintenancePanel.tsx:250`), et
  // `getByText('ancien.md')` échouerait alors immédiatement.
  await attendreLesDiagnostics()

  await user.click(screen.getByRole('tab', { name: /Reprise/ }))
  expect(screen.getByText('ancien.md')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Recalculer (1)' }))
  expect(startBackfill).toHaveBeenCalledWith('project-1')

  // SECONDE fenêtre du même défaut, trouvée en éprouvant le correctif sous une
  // API lente : le clic ci-dessus déclenche `onDone`, qui rappelle `load()`,
  // qui remet `loading` à vrai — et l'onglet Backfill rebascule alors sur
  // « Analyse des médias disponibles… » (`MaintenancePanel.tsx:250`). Un
  // rafraîchissement de fond peut donc MASQUER ce qu'on interroge juste après.
  //
  // Corriger la seule synchronisation initiale aurait laissé ce trou ouvert.
  expect(await screen.findByText('archive.md')).toBeInTheDocument()
  expect(await screen.findByText(/1 document reste impossible/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Créer les copies (1)' }))
  expect(startFaithfulBackfill).toHaveBeenCalledWith('project-1')

  await user.click(screen.getByRole('tab', { name: /Doublons/ }))
  await waitFor(() => expect(getDuplicates).toHaveBeenCalledWith('project-1'))
  expect(await screen.findByText(/Aucun doublon détecté/)).toBeInTheDocument()

  await user.click(screen.getByRole('tab', { name: 'Corbeille' }))
  expect(await screen.findByText(/Aucun élément archivé/)).toBeInTheDocument()
  expect(getTrash).toHaveBeenCalledWith('project-1')
})


it('pour un membre : doublons et corbeille seulement, sans diagnostic de la bibliothèque', async () => {
  vi.mocked(getMaintenanceStatus).mockClear()
  render(<MaintenancePanel viewScope="all" scopeLabel="Tous mes projets" admin={false} />)

  expect(await screen.findByRole('tab', { name: /Doublons/ })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tab', { name: /Corbeille/ })).toBeInTheDocument()
  expect(screen.queryByRole('tab', { name: /État & index/ })).not.toBeInTheDocument()
  await waitFor(() => expect(getTrash).toHaveBeenCalled())
  expect(getMaintenanceStatus).not.toHaveBeenCalled()
})


it('un diagnostic qui échoue dit la phrase du contexte ; le détail brut est replié, pour l’administrateur', async () => {
  vi.mocked(getMaintenanceStatus).mockRejectedValue(new Error('HTTP 500: {"detail":"diagnostic-brut"}'))
  render(<MaintenancePanel viewScope="project-1" scopeLabel="Client" admin />)

  expect(await screen.findByText('L’état de la maintenance n’a pas pu être chargé.')).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('L’état de la maintenance n’a pas pu être chargé.')   // annoncée
  expect(screen.queryByText('Lecture des diagnostics…')).toBeNull()   // la lecture est finie : l'échec ne reste pas à côté d'un « en cours »
  const detail = document.querySelector('details') as HTMLDetailsElement
  expect(detail.open).toBe(false)
  expect(detail).toHaveTextContent('diagnostic-brut')
  expect(screen.getByText('Détail technique')).toBeInTheDocument()
})


it('une réindexation qui échoue dit la phrase du contexte ; le détail brut est replié, pour l’administrateur', async () => {
  const user = userEvent.setup()
  vi.mocked(reindexMaintenance).mockRejectedValue(new Error('HTTP 500: reindexation-brute'))
  render(<MaintenancePanel viewScope="project-1" scopeLabel="Client" admin />)
  await attendreLesDiagnostics()

  await user.click(screen.getByRole('button', { name: 'Rattacher' }))

  expect(await screen.findByText('L’opération n’a pas abouti.')).toBeInTheDocument()
  expect((document.querySelector('details') as HTMLDetailsElement).open).toBe(false)
  expect(document.querySelector('details')).toHaveTextContent('reindexation-brute')
})


it('un retraitement dont le suivi échoue dit la phrase du contexte ; le détail du flux est replié', async () => {
  const user = userEvent.setup()
  vi.mocked(streamJob).mockImplementation((_jobId, handlers) => {
    handlers.onError?.('flux-brut-du-serveur')
    return () => {}
  })
  render(<MaintenancePanel viewScope="project-1" scopeLabel="Client" admin />)
  await attendreLesDiagnostics()
  await user.click(screen.getByRole('tab', { name: /Reprise/ }))

  await user.click(screen.getByRole('button', { name: 'Recalculer (1)' }))

  expect(await screen.findByText('L’opération n’a pas abouti.')).toBeInTheDocument()
  expect(document.querySelector('details')).toHaveTextContent('flux-brut-du-serveur')
})


it('changer de langue garde la section ouverte et ne relit pas l’état de la maintenance', async () => {
  const user = userEvent.setup()
  render(<LangProvider initiale="fr"><LangSelector /><MaintenancePanel viewScope="project-1" scopeLabel="Client" admin /></LangProvider>)
  await attendreLesDiagnostics()
  await user.click(screen.getByRole('tab', { name: 'Corbeille' }))
  expect(screen.getByRole('tab', { name: 'Corbeille' })).toHaveAttribute('aria-selected', 'true')
  const appelsAvant = vi.mocked(getMaintenanceStatus).mock.calls.length

  await user.selectOptions(screen.getByRole('combobox'), 'en')

  await waitFor(() => expect(screen.getByRole('tab', { name: /Trash/ })).toHaveAttribute('aria-selected', 'true'))
  expect(vi.mocked(getMaintenanceStatus).mock.calls.length).toBe(appelsAvant)
})


it('pour un membre, les flèches ne visitent que Doublons et Corbeille, en faisant le tour', async () => {
  const user = userEvent.setup()
  render(<MaintenancePanel viewScope="all" scopeLabel="Tous mes projets" admin={false} />)
  const doublons = await screen.findByRole('tab', { name: /Doublons/ })
  doublons.focus()

  await user.keyboard('{ArrowRight}')
  const corbeille = screen.getByRole('tab', { name: /Corbeille/ })
  expect(corbeille).toHaveFocus()
  expect(corbeille).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', corbeille.id)   // le panneau porte le nom de son onglet

  await user.keyboard('{ArrowRight}')
  expect(screen.getByRole('tab', { name: /Doublons/ })).toHaveFocus()   // retour au premier : seuls les onglets présents comptent
})


it('l’onglet sélectionné pointe toujours un panneau présent : pendant le chargement et après un échec', async () => {
  vi.mocked(getMaintenanceStatus).mockReturnValue(new Promise(() => {}))   // la lecture ne finit pas
  const attente = render(<MaintenancePanel viewScope="project-1" scopeLabel="Client" admin />)
  const onglet = screen.getByRole('tab', { selected: true })
  expect(document.getElementById(onglet.getAttribute('aria-controls') as string)).not.toBeNull()
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', onglet.id)
  attente.unmount()

  vi.mocked(getMaintenanceStatus).mockRejectedValue(new Error('HTTP 500: panne'))
  render(<MaintenancePanel viewScope="project-1" scopeLabel="Client" admin />)
  await screen.findByRole('alert')
  const apres = screen.getByRole('tab', { selected: true })
  expect(document.getElementById(apres.getAttribute('aria-controls') as string)).not.toBeNull()
})
