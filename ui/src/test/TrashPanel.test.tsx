import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import {
  deleteTrashItem, emptyTrash, getTrash, restoreTrashItem,
} from '../api'
import { LangProvider } from '../components/LangProvider'
import { TrashPanel } from '../components/TrashPanel'
import { aucuneFenetreDeConfirmation, avecConfirmation, confirmer as confirmerLaFenetre, fenetreDeConfirmation, phraseDeLaFenetre, refuser } from './confirmation'


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
})


it('affiche origine projet date taille et nature de chaque élément', async () => {
  render(<TrashPanel viewScope="project-1" />, { wrapper: avecConfirmation })

  expect(await screen.findByText('Réunion produit')).toBeInTheDocument()
  expect(screen.getByText('Réunion produit — compte rendu')).toBeInTheDocument()
  expect(screen.getAllByText('Client')).toHaveLength(2)
  expect(screen.getByText('23/07/2026 08:30')).toBeInTheDocument()
  expect(screen.getByText('1,5 Ko')).toBeInTheDocument()
  expect(screen.getByText('Compte rendu')).toBeInTheDocument()
  expect(getTrash).toHaveBeenCalledWith('project-1')
})


it('restaure explicitement un élément dans son projet d’origine', async () => {
  const user = userEvent.setup()
  render(<TrashPanel viewScope="project-1" />, { wrapper: avecConfirmation })
  await screen.findByText('Réunion produit')

  await user.click(screen.getByRole('button', { name: 'Restaurer « Réunion produit »' }))

  expect(restoreTrashItem).toHaveBeenCalledWith('réunion.md', 'project-1')
  expect(await screen.findByText(/restauré dans « Client »/)).toBeInTheDocument()
})


it('nomme toutes les traces détruites avant une suppression définitive', async () => {
  const user = userEvent.setup()
  render(<TrashPanel viewScope="project-1" />, { wrapper: avecConfirmation })
  await screen.findByText('Réunion produit')

  await user.click(screen.getByRole('button', {
    name: 'Supprimer définitivement « Réunion produit — compte rendu »',
  }))

  expect(await fenetreDeConfirmation()).toHaveAccessibleName('Supprimer définitivement « Réunion produit — compte rendu » ?')
  expect(await phraseDeLaFenetre()).toContain('le Markdown archivé, l’index, les profils de voix et les archives audio')
  expect(within(await fenetreDeConfirmation()).getByRole('button', { name: 'Supprimer définitivement' })).toBeInTheDocument()
  expect(deleteTrashItem).not.toHaveBeenCalled()
  await confirmerLaFenetre(user)
  expect(deleteTrashItem).toHaveBeenCalledWith('réunion-cr.md', 'project-1')
})


it('une suppression définitive REFUSÉE ne supprime rien', async () => {
  const user = userEvent.setup()
  render(<TrashPanel viewScope="project-1" />, { wrapper: avecConfirmation })
  await screen.findByText('Réunion produit')

  await user.click(screen.getByRole('button', { name: 'Supprimer définitivement « Réunion produit — compte rendu »' }))
  await refuser(user)

  aucuneFenetreDeConfirmation()
  expect(deleteTrashItem).not.toHaveBeenCalled()
})


it('vider la corbeille REFUSÉ ne vide rien', async () => {
  const user = userEvent.setup()
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })
  await screen.findByText('Réunion produit')

  await user.click(screen.getByRole('button', { name: 'Vider cette corbeille' }))
  expect(within(await fenetreDeConfirmation()).getByRole('button', { name: 'Vider la corbeille' })).toBeInTheDocument()
  await refuser(user)

  aucuneFenetreDeConfirmation()
  expect(emptyTrash).not.toHaveBeenCalled()
})


it('vide seulement la portée affichée et annonce les échecs reprenables', async () => {
  const user = userEvent.setup()
  vi.mocked(emptyTrash).mockRejectedValue(new Error(
    'HTTP 500: Nettoyage partiel : 1 supprimé, 1 conservé',
  ))
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })
  await screen.findByText('Réunion produit')

  await user.click(screen.getByRole('button', { name: 'Vider cette corbeille' }))

  expect(await fenetreDeConfirmation()).toHaveAccessibleName(/2 éléments du projet « Client »/)
  expect(emptyTrash).not.toHaveBeenCalled()
  await confirmerLaFenetre(user)
  expect(emptyTrash).toHaveBeenCalledWith('project-1')
  // La liste rechargée est la même : l'échec est annoncé, et dit que rien n'a été supprimé.
  expect(await screen.findByText(/Rien n’a été supprimé/)).toBeInTheDocument()
  await waitFor(() => expect(getTrash).toHaveBeenCalledTimes(2))
})


it('nomme un élément par son titre lisible, jamais par le slug de son fichier', async () => {
  vi.mocked(getTrash).mockResolvedValue([
    { ...ITEMS[0], name: 'ami-is1009a-reunion.md', origin: 'ami-is1009a-reunion.md',
      title: 'ami-is1009a-reunion', source: 'AMI IS1009a, réunion.wav' },
    { ...ITEMS[1], name: 'ami-is1009a-reunion-cr.md', origin: 'ami-is1009a-reunion-cr.md',
      title: 'ami-is1009a-reunion — compte-rendu', source: 'AMI IS1009a, réunion.wav' },
  ])
  render(<TrashPanel viewScope="project-1" />, { wrapper: avecConfirmation })

  expect(await screen.findByText('AMI IS1009a, réunion')).toBeInTheDocument()
  expect(screen.getByText('AMI IS1009a, réunion — compte rendu')).toBeInTheDocument()
  expect(screen.queryByText('ami-is1009a-reunion')).not.toBeInTheDocument()
  expect(screen.queryByText('ami-is1009a-reunion — compte-rendu')).not.toBeInTheDocument()
})


const PHRASE_ECHEC = 'La corbeille n’a pas pu être chargée.'
const ECHEC_SERVEUR = new Error('HTTP 503: {"detail":"base indisponible"}')

/** Une promesse qu'on résout ou qu'on rejette à la main, pour tenir un chargement « en cours ». */
function enAttente<T>() {
  let resoudre!: (v: T) => void
  let rejeter!: (e: unknown) => void
  const promesse = new Promise<T>((ok, ko) => { resoudre = ok; rejeter = ko })
  return { promesse, resoudre, rejeter }
}


it('un chargement qui échoue le dit et propose « Réessayer » : jamais « Aucun élément archivé », jamais le texte de l’exception', async () => {
  vi.mocked(getTrash).mockRejectedValue(ECHEC_SERVEUR)
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })

  expect(await screen.findByRole('alert')).toHaveTextContent(PHRASE_ECHEC)
  expect(screen.getByRole('button', { name: 'Réessayer' })).toBeInTheDocument()
  expect(screen.queryByText('Aucun élément archivé.')).toBeNull()
  expect(document.body.textContent).not.toMatch(/HTTP|base indisponible/)
  expect(screen.getByRole('button', { name: 'Vider cette corbeille' })).toBeDisabled()
})


it('témoin : une corbeille réellement vide dit « Aucun élément archivé », sans alerte', async () => {
  vi.mocked(getTrash).mockResolvedValue([])
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })

  expect(await screen.findByText('Aucun élément archivé.')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
})


it('un chargement lent n’affiche ni l’échec ni « Aucun élément archivé » pendant l’attente', async () => {
  const lent = enAttente<typeof ITEMS>()
  vi.mocked(getTrash).mockReturnValue(lent.promesse)
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })

  expect(screen.getByText('Lecture de la corbeille…')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByText('Aucun élément archivé.')).toBeNull()

  lent.resoudre(ITEMS)
  expect(await screen.findByText('Réunion produit')).toBeInTheDocument()
})


it('« Réessayer » repasse par l’attente, efface l’échec et affiche la liste quand le chargement réussit', async () => {
  const user = userEvent.setup()
  const seconde = enAttente<typeof ITEMS>()
  vi.mocked(getTrash).mockRejectedValueOnce(ECHEC_SERVEUR).mockReturnValueOnce(seconde.promesse)
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })

  await user.click(await screen.findByRole('button', { name: 'Réessayer' }))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText('Lecture de la corbeille…')).toBeInTheDocument()

  seconde.resoudre(ITEMS)
  expect(await screen.findByText('Réunion produit')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
})


it('un second échec qui suit le « Réessayer » de la personne redonne le focus au bouton', async () => {
  const user = userEvent.setup()
  vi.mocked(getTrash).mockRejectedValue(ECHEC_SERVEUR)
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })

  await user.click(await screen.findByRole('button', { name: 'Réessayer' }))
  await waitFor(() => expect(getTrash).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Réessayer' })).toHaveFocus())
})


it('un échec qui suit une liste déjà affichée remplace cette liste : on ne vide ni ne supprime depuis une liste périmée', async () => {
  const user = userEvent.setup()
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })
  expect(await screen.findByText('Réunion produit')).toBeInTheDocument()

  vi.mocked(getTrash).mockRejectedValueOnce(ECHEC_SERVEUR)
  await user.click(screen.getByRole('button', { name: 'Actualiser' }))

  expect(await screen.findByRole('alert')).toHaveTextContent(PHRASE_ECHEC)
  expect(screen.queryByText('Réunion produit')).toBeNull()
  expect(screen.getByRole('button', { name: 'Vider cette corbeille' })).toBeDisabled()
})


it('l’échec d’un chargement périmé (la portée a changé entre-temps) ne remplace pas la réponse la plus récente', async () => {
  const ancien = enAttente<typeof ITEMS>()
  vi.mocked(getTrash).mockReturnValueOnce(ancien.promesse).mockResolvedValueOnce(ITEMS)
  const { rerender } = render(<TrashPanel viewScope="projet-a" scopeLabel="A" />, { wrapper: avecConfirmation })
  rerender(<TrashPanel viewScope="projet-b" scopeLabel="B" />)

  expect(await screen.findByText('Réunion produit')).toBeInTheDocument()
  ancien.rejeter(ECHEC_SERVEUR)
  await new Promise((fin) => setTimeout(fin, 20))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText('Réunion produit')).toBeInTheDocument()
})


it('l’échec d’une ACTION (restaurer) garde la liste : seul le chargement la remplace par l’état d’échec', async () => {
  const user = userEvent.setup()
  vi.mocked(restoreTrashItem).mockRejectedValue(new Error('refus de la restauration'))
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })
  await screen.findByText('Réunion produit')

  await user.click(screen.getAllByRole('button', { name: /Restaurer/ })[0])

  await waitFor(() => expect(restoreTrashItem).toHaveBeenCalled())
  expect(screen.getByText('Réunion produit')).toBeInTheDocument()
  expect(screen.queryByText(PHRASE_ECHEC)).toBeNull()
})


it('en anglais, la phrase d’échec et l’action de reprise sont en anglais', async () => {
  vi.mocked(getTrash).mockRejectedValue(ECHEC_SERVEUR)
  render(<LangProvider initiale="en"><TrashPanel viewScope="project-1" scopeLabel="Client" /></LangProvider>)

  expect(await screen.findByRole('alert')).toHaveTextContent('The trash could not be loaded.')
  expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
})


it('en anglais, la date d’archivage se lit « 23 Jul 2026, 08:30 », jamais « 23/07/2026 08:30 »', async () => {
  render(<LangProvider initiale="en"><TrashPanel viewScope="project-1" scopeLabel="Client" /></LangProvider>)

  expect(await screen.findByText('23 Jul 2026, 08:30')).toBeInTheDocument()
  expect(screen.queryByText('23/07/2026 08:30')).toBeNull()
})


it('l’échec d’une restauration dit la phrase du contexte : ni « HTTP », ni le texte de l’exception', async () => {
  const user = userEvent.setup()
  vi.mocked(restoreTrashItem).mockRejectedValue(new Error('HTTP 500: restauration-brute'))
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })
  await screen.findByText('Réunion produit')

  await user.click(screen.getAllByRole('button', { name: /Restaurer/ })[0])

  expect(await screen.findByText('La restauration n’a pas abouti.')).toBeInTheDocument()
  expect(document.body.textContent).not.toMatch(/HTTP|restauration-brute/)
})


it('l’échec d’une suppression dit la phrase du contexte : ni « HTTP », ni le texte de l’exception', async () => {
  const user = userEvent.setup()
  vi.mocked(deleteTrashItem).mockRejectedValue(new Error('HTTP 500: suppression-brute'))
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })
  await screen.findByText('Réunion produit')

  await user.click(screen.getAllByRole('button', { name: /Supprimer définitivement/ })[0])
  await confirmerLaFenetre(user)

  expect(await screen.findByText('La suppression n’a pas abouti : l’élément conservé reste visible et la tentative peut être relancée.')).toBeInTheDocument()
  expect(document.body.textContent).not.toMatch(/HTTP|suppression-brute/)
})


it('l’échec d’un vidage dit la phrase du contexte : ni « HTTP », ni le décompte brut du serveur', async () => {
  const user = userEvent.setup()
  vi.mocked(emptyTrash).mockRejectedValue(new Error('HTTP 500: Nettoyage partiel : 1 supprimé-brut, 1 conservé'))
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })
  await screen.findByText('Réunion produit')

  await user.click(screen.getByRole('button', { name: 'Vider cette corbeille' }))
  await confirmerLaFenetre(user)

  // La liste rechargée est la même : rien n'a été supprimé, la phrase ne dit pas « partiel ».
  expect(await screen.findByText('Rien n’a été supprimé : la corbeille est intacte et la tentative peut être relancée.')).toBeInTheDocument()
  expect(document.body.textContent).not.toMatch(/HTTP|supprimé-brut|Nettoyage partiel/)
})


it('un vidage qui échoue après avoir supprimé une partie dit « Nettoyage partiel »', async () => {
  const user = userEvent.setup()
  vi.mocked(emptyTrash).mockRejectedValue(new Error('HTTP 500: Nettoyage partiel : 1 supprimé, 1 conservé'))
  render(<TrashPanel viewScope="project-1" scopeLabel="Client" />, { wrapper: avecConfirmation })
  await screen.findByText('Réunion produit')
  vi.mocked(getTrash).mockResolvedValue([ITEMS[0]])     // la liste rechargée a perdu un élément

  await user.click(screen.getByRole('button', { name: 'Vider cette corbeille' }))
  await confirmerLaFenetre(user)

  expect(await screen.findByText('Nettoyage partiel : les éléments conservés restent visibles et la tentative peut être relancée.')).toBeInTheDocument()
})

