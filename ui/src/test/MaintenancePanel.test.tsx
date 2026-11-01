import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import {
  getDuplicates, getMaintenanceStatus, getTrash, reindexMaintenance, startBackfill,
  startFaithfulBackfill, streamJob,
} from '../api'
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
 * « Portée : … » N'EST PAS un point de synchronisation.
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
  expect(await screen.findByText('Portée : Projet Client')).toBeInTheDocument()
  return screen.findByText('3/4')
}


it('borne diagnostics et réindexations au projet courant', async () => {
  const user = userEvent.setup()
  render(<MaintenancePanel viewScope="project-1" scopeLabel="Client" />)

  expect(await attendreLesDiagnostics()).toBeInTheDocument()
  expect(screen.getByText(/1 document sans index sémantique/)).toBeInTheDocument()
  expect(getMaintenanceStatus).toHaveBeenCalledWith('project-1')

  await user.click(screen.getByRole('button', { name: 'Réindexer les voix' }))
  await waitFor(() => expect(reindexMaintenance).toHaveBeenCalledWith('voices', 'project-1'))
})


it('regroupe backfill et doublons sans perdre la portée', async () => {
  const user = userEvent.setup()
  render(<MaintenancePanel viewScope="project-1" scopeLabel="Client" />)
  // Même raison qu'au-dessus : l'onglet Backfill affiche « Analyse des médias
  // disponibles… » tant que `status` est nul (`MaintenancePanel.tsx:250`), et
  // `getByText('ancien.md')` échouerait alors immédiatement.
  await attendreLesDiagnostics()

  await user.click(screen.getByRole('tab', { name: /Backfill/ }))
  expect(screen.getByText('ancien.md')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Recalculer les empreintes (1)' }))
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
  await user.click(screen.getByRole('button', { name: 'Créer les archives (1)' }))
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
