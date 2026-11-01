import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import {
  deleteTrashItem, emptyTrash, getTrash, restoreTrashItem,
} from '../api'
import { TrashPanel } from '../components/TrashPanel'


vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getTrash: vi.fn(),
    restoreTrashItem: vi.fn(),
    deleteTrashItem: vi.fn(),
    emptyTrash: vi.fn(),
  }
})

const ITEMS = [
  {
    name: 'réunion.md',
    origin: 'réunion.md',
    project: 'Client',
    document_id: 'doc-1',
    project_id: 'project-1',
    meeting_id: 'meeting-1',
    title: 'Réunion produit',
    type: null,
    source_transcript: null,
    source_document_id: null,
    archived_at: '2026-07-23T08:30:00',
    size: 1536,
    modified: 1,
  },
  {
    name: 'réunion-cr.md',
    origin: 'réunion-cr.md',
    project: 'Client',
    document_id: 'doc-2',
    project_id: 'project-1',
    meeting_id: 'meeting-1',
    title: 'Réunion produit — compte-rendu',
    type: 'report',
    source_transcript: 'réunion.md',
    source_document_id: 'doc-1',
    archived_at: '2026-07-23T08:31:00',
    size: 2048,
    modified: 2,
  },
]


beforeEach(() => {
  vi.mocked(getTrash).mockResolvedValue(ITEMS)
  vi.mocked(restoreTrashItem).mockResolvedValue({
    restored: 'réunion.md', project: 'Client', warnings: [],
  })
  vi.mocked(deleteTrashItem).mockResolvedValue({
    deleted: 'réunion-cr.md',
    origin: 'réunion-cr.md',
    clean: true,
    shared_artifacts_preserved: false,
    freed_bytes: 2048,
    errors: [],
  })
  vi.mocked(emptyTrash).mockResolvedValue({
    removed: 2, freed_bytes: 3584, kept: [], errors: [],
  })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})


it('affiche origine projet date taille et nature de chaque élément', async () => {
  render(<TrashPanel viewScope="project-1" />)

  expect(await screen.findByText('Réunion produit')).toBeInTheDocument()
  expect(screen.getByText('Réunion produit — compte-rendu')).toBeInTheDocument()
  expect(screen.getAllByText('Client')).toHaveLength(2)
  expect(screen.getByText('23/07/2026 08:30')).toBeInTheDocument()
  expect(screen.getByText('1,5 Ko')).toBeInTheDocument()
  expect(screen.getByText('Compte-rendu')).toBeInTheDocument()
  expect(getTrash).toHaveBeenCalledWith('project-1')
})


it('restaure explicitement un élément dans son projet d’origine', async () => {
  const user = userEvent.setup()
  render(<TrashPanel viewScope="project-1" />)
  await screen.findByText('Réunion produit')

  await user.click(screen.getByRole('button', { name: 'Restaurer « Réunion produit »' }))

  expect(restoreTrashItem).toHaveBeenCalledWith('réunion.md', 'project-1')
  expect(await screen.findByText(/restauré dans « Client »/)).toBeInTheDocument()
})


it('nomme toutes les traces détruites avant une suppression définitive', async () => {
  const user = userEvent.setup()
  render(<TrashPanel viewScope="project-1" />)
  await screen.findByText('Réunion produit')

  await user.click(screen.getByRole('button', {
    name: 'Supprimer définitivement « Réunion produit — compte-rendu »',
  }))

  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining(
    'Markdown archivé, index, vecteurs, voix, empreintes et archives audio',
  ))
  expect(deleteTrashItem).toHaveBeenCalledWith('réunion-cr.md', 'project-1')
})


it('vide seulement la portée affichée et annonce les échecs reprenables', async () => {
  const user = userEvent.setup()
  vi.mocked(emptyTrash).mockRejectedValue(new Error(
    'HTTP 500: Nettoyage partiel : 1 supprimé, 1 conservé',
  ))
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />)
  await screen.findByText('Réunion produit')

  await user.click(screen.getByRole('button', { name: 'Vider cette corbeille' }))

  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining(
    '2 éléments du projet « Client »',
  ))
  expect(emptyTrash).toHaveBeenCalledWith('project-1')
  expect(await screen.findByText(/Nettoyage partiel/)).toBeInTheDocument()
  await waitFor(() => expect(getTrash).toHaveBeenCalledTimes(2))
})


it('nomme un élément par son titre lisible, jamais par le slug de son fichier', async () => {
  vi.mocked(getTrash).mockResolvedValue([
    { ...ITEMS[0], name: 'ami-is1009a-reunion.md', origin: 'ami-is1009a-reunion.md',
      title: 'ami-is1009a-reunion', source: 'AMI IS1009a, réunion.wav' },
    { ...ITEMS[1], name: 'ami-is1009a-reunion-cr.md', origin: 'ami-is1009a-reunion-cr.md',
      title: 'ami-is1009a-reunion — compte-rendu', source: 'AMI IS1009a, réunion.wav' },
  ])
  render(<TrashPanel viewScope="project-1" />)

  expect(await screen.findByText('AMI IS1009a, réunion')).toBeInTheDocument()
  expect(screen.getByText('AMI IS1009a, réunion — compte-rendu')).toBeInTheDocument()
  expect(screen.queryByText('ami-is1009a-reunion')).not.toBeInTheDocument()
  expect(screen.queryByText('ami-is1009a-reunion — compte-rendu')).not.toBeInTheDocument()
})
