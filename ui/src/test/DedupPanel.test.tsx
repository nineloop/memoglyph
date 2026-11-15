import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import {
  archiveTranscript, getArchivePlan, getDuplicates,
} from '../api'
import { DedupPanel } from '../components/DedupPanel'
import { LangProvider } from '../components/LangProvider'
import { aucuneFenetreDeConfirmation, avecConfirmation, confirmer as confirmerLaFenetre, fenetreDeConfirmation, phraseDeLaFenetre, refuser } from './confirmation'


vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getDuplicates: vi.fn(),
    getArchivePlan: vi.fn(),
    archiveTranscript: vi.fn(),
  }
})


beforeEach(() => {
  vi.mocked(getDuplicates).mockResolvedValue([{
    keep: 'version-récente.md',
    reason: 'version la plus récente',
    count: 2,
    source: 'réunion.m4a',
    members: [
      {
        name: 'version-récente.md',
        title: 'Version récente',
        source: 'réunion.m4a',
        language: 'fr',
        duration: '10:00',
        diarization: true,
        profiled: true,
        n_speakers: 2,
        generated_at: '2026-07-23',
        keep: true,
      },
      {
        name: 'ancienne.md',
        title: 'Ancienne version',
        source: 'réunion.m4a',
        language: 'fr',
        duration: '10:00',
        diarization: true,
        profiled: false,
        n_speakers: 2,
        generated_at: '2026-07-22',
        keep: false,
      },
    ],
  }])
  vi.mocked(getArchivePlan).mockResolvedValue({
    requested: 'ancienne.md',
    documents: [
      {
        name: 'ancienne-cr.md',
        title: 'Ancienne version — compte-rendu',
        type: 'report',
        document_id: 'doc-cr',
      },
      {
        name: 'ancienne.md',
        title: 'Ancienne version',
        type: null,
        document_id: 'doc-source',
      },
    ],
  })
  vi.mocked(archiveTranscript).mockResolvedValue({
    archived: 'ancienne.md',
    documents: ['ancienne-cr.md', 'ancienne.md'],
  })
})


it('annonce le transcript et ses comptes-rendus avant archivage', async () => {
  const user = userEvent.setup()
  render(<DedupPanel viewScope="project-1" embedded />, { wrapper: avecConfirmation })

  await user.click(await screen.findByRole('button', { name: 'Archiver' }))

  expect(getArchivePlan).toHaveBeenCalledWith('ancienne.md', 'project-1')
  const fenetre = await fenetreDeConfirmation()
  expect(fenetre).toHaveAccessibleName('Archiver 2 documents liés ?')
  // Les documents annoncés sont rendus en liste, un par ligne.
  expect(within(within(fenetre).getByRole('list')).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
    'Ancienne version — compte-rendu (ancienne-cr.md)', 'Ancienne version (ancienne.md)',
  ])
  expect(await phraseDeLaFenetre()).toContain('bibliothèque, de la recherche et de l’assistant')
  expect(await phraseDeLaFenetre()).toContain('archives audio et profils de voix restent récupérables')
  expect(within(fenetre).getByRole('button', { name: 'Archiver' })).toBeInTheDocument()
  expect(archiveTranscript).not.toHaveBeenCalled()
  await confirmerLaFenetre(userEvent)
  expect(archiveTranscript).toHaveBeenCalledWith('ancienne.md', 'project-1')
  await waitFor(() => expect(getDuplicates).toHaveBeenCalledTimes(2))
})


it('n’archive rien quand la confirmation est refusée', async () => {
  const user = userEvent.setup()
  render(<DedupPanel viewScope="project-1" embedded />, { wrapper: avecConfirmation })

  await user.click(await screen.findByRole('button', { name: 'Archiver' }))
  await refuser(user)

  expect(getArchivePlan).toHaveBeenCalledWith('ancienne.md', 'project-1')
  aucuneFenetreDeConfirmation()
  expect(archiveTranscript).not.toHaveBeenCalled()
})


it('l’échec d’un archivage se dit sur l’écran, en une phrase : ni boîte native, ni texte brut', async () => {
  const user = userEvent.setup()
  const alerte = vi.spyOn(window, 'alert').mockImplementation(() => {})
  vi.mocked(archiveTranscript).mockRejectedValue(new Error('HTTP 500: {"detail":"archivage-brut"}'))
  render(<DedupPanel viewScope="project-1" embedded />, { wrapper: avecConfirmation })

  await user.click(await screen.findByRole('button', { name: 'Archiver' }))
  await confirmerLaFenetre(user)

  expect(await screen.findByRole('alert')).toHaveTextContent('L’archivage n’a pas abouti.')
  expect(document.body.textContent).not.toMatch(/HTTP|archivage-brut/)
  expect(alerte).not.toHaveBeenCalled()
  alerte.mockRestore()
})


it('titre un groupe par le titre lisible de la réunion, ni slug ni extension', async () => {
  vi.mocked(getDuplicates).mockResolvedValue([{
    keep: 'ami-is1009a-reunion.md',
    reason: 'version la plus récente',
    count: 2,
    source: 'AMI IS1009a, réunion.wav',
    members: [
      { name: 'ami-is1009a-reunion.md', title: 'ami-is1009a-reunion', source: 'AMI IS1009a, réunion.wav',
        language: 'fr', duration: '10:00', diarization: true, profiled: true, n_speakers: 2,
        generated_at: '2026-07-23', keep: true },
      { name: 'ami-is1009a-reunion-1.md', title: 'ami-is1009a-reunion', source: 'AMI IS1009a, réunion.wav',
        language: 'fr', duration: '10:00', diarization: false, profiled: false, n_speakers: null,
        generated_at: '2026-07-22', keep: false },
    ],
  }])
  render(<DedupPanel viewScope="project-1" />, { wrapper: avecConfirmation })

  expect(await screen.findByText('AMI IS1009a, réunion')).toBeInTheDocument()
  expect(screen.queryByText('AMI IS1009a, réunion.wav')).not.toBeInTheDocument()
  expect(screen.queryByText('ami-is1009a-reunion')).not.toBeInTheDocument()
})


const PHRASE_ECHEC = 'Les doublons n’ont pas pu être chargés.'
const ECHEC_RESEAU = new Error('HTTP 500: {"detail":"boom interne"}')

/** Une promesse qu'on résout ou qu'on rejette à la main, pour tenir un chargement « en cours ». */
function enAttente<T>() {
  let resoudre!: (v: T) => void
  let rejeter!: (e: unknown) => void
  const promesse = new Promise<T>((ok, ko) => { resoudre = ok; rejeter = ko })
  return { promesse, resoudre, rejeter }
}


it('un chargement qui échoue le dit, propose « Réessayer » et n’affirme jamais qu’il n’y a aucun doublon', async () => {
  vi.mocked(getDuplicates).mockRejectedValue(ECHEC_RESEAU)
  render(<DedupPanel viewScope="project-1" embedded />, { wrapper: avecConfirmation })

  expect(await screen.findByRole('alert')).toHaveTextContent(PHRASE_ECHEC)
  expect(screen.getByRole('button', { name: 'Réessayer' })).toBeInTheDocument()
  expect(screen.queryByText('Aucun doublon détecté.')).toBeNull()
  // Le texte de l'exception n'est jamais montré.
  expect(document.body.textContent).not.toMatch(/HTTP|boom interne/)
})


it('témoin : une liste réellement vide dit qu’il n’y a aucun doublon, sans alerte', async () => {
  vi.mocked(getDuplicates).mockResolvedValue([])
  render(<DedupPanel viewScope="project-1" embedded />, { wrapper: avecConfirmation })

  expect(await screen.findByText('Aucun doublon détecté.')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
})


it('un chargement lent puis réussi n’affiche ni l’échec ni le vide pendant l’attente', async () => {
  const lent = enAttente<never[]>()
  vi.mocked(getDuplicates).mockReturnValue(lent.promesse)
  render(<DedupPanel viewScope="project-1" embedded />, { wrapper: avecConfirmation })

  expect(screen.getByText(/Analyse du corpus/)).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByText('Aucun doublon détecté.')).toBeNull()

  lent.resoudre([])
  expect(await screen.findByText('Aucun doublon détecté.')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
})


it('« Réessayer » repasse par l’attente, efface l’échec et affiche la liste quand le chargement réussit', async () => {
  const user = userEvent.setup()
  const donnees = await vi.mocked(getDuplicates).getMockImplementation()?.('') ?? []
  const seconde = enAttente<typeof donnees>()
  vi.mocked(getDuplicates).mockRejectedValueOnce(ECHEC_RESEAU).mockReturnValueOnce(seconde.promesse)
  render(<DedupPanel viewScope="project-1" embedded />, { wrapper: avecConfirmation })

  await user.click(await screen.findByRole('button', { name: 'Réessayer' }))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText(/Analyse du corpus/)).toBeInTheDocument()

  seconde.resoudre(donnees)
  expect(await screen.findByRole('button', { name: 'Archiver' })).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
})


it('un second échec qui suit le « Réessayer » de la personne redonne le focus au bouton', async () => {
  const user = userEvent.setup()
  vi.mocked(getDuplicates).mockRejectedValue(ECHEC_RESEAU)
  render(<DedupPanel viewScope="project-1" embedded />, { wrapper: avecConfirmation })

  await user.click(await screen.findByRole('button', { name: 'Réessayer' }))
  await waitFor(() => expect(getDuplicates).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Réessayer' })).toHaveFocus())
})


it('l’échec d’un chargement périmé (la portée a changé entre-temps) ne remplace pas la réponse la plus récente', async () => {
  const ancien = enAttente<never[]>()
  vi.mocked(getDuplicates).mockReturnValueOnce(ancien.promesse).mockResolvedValueOnce([])
  const { rerender } = render(<DedupPanel viewScope="projet-a" embedded />, { wrapper: avecConfirmation })
  rerender(<DedupPanel viewScope="projet-b" embedded />)

  expect(await screen.findByText('Aucun doublon détecté.')).toBeInTheDocument()
  ancien.rejeter(ECHEC_RESEAU)
  await new Promise((fin) => setTimeout(fin, 20))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText('Aucun doublon détecté.')).toBeInTheDocument()
})


it('en anglais, la phrase d’échec et l’action de reprise sont en anglais', async () => {
  vi.mocked(getDuplicates).mockRejectedValue(ECHEC_RESEAU)
  render(<LangProvider initiale="en"><DedupPanel viewScope="project-1" embedded /></LangProvider>)

  expect(await screen.findByRole('alert')).toHaveTextContent('Duplicates could not be loaded.')
  expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  expect(screen.queryByText('No duplicate detected.')).toBeNull()
})


it('la raison du groupe se lit dans la langue de l’interface, jamais la phrase française du serveur', async () => {
  vi.mocked(getDuplicates).mockResolvedValue([{
    keep: 'gardee.md', reason: 'le plus complet — diarisé + profilé', count: 2, source: 'reunion.wav',
    members: [
      { name: 'gardee.md', title: 'Comité', source: 'reunion.wav', language: 'fr', duration: '10:00', diarization: true, profiled: true, n_speakers: 2, generated_at: '2026-07-23', keep: true },
      { name: 'ancienne.md', title: 'Comité', source: 'reunion.wav', language: 'fr', duration: '10:00', diarization: false, profiled: false, n_speakers: null, generated_at: '2026-07-22', keep: false },
    ],
  }])
  render(<LangProvider initiale="en"><DedupPanel viewScope="project-1" embedded /></LangProvider>, { wrapper: avecConfirmation })

  expect(await screen.findByText('Keep: the most complete (speakers, summary and themes)')).toBeInTheDocument()
  expect(document.body.textContent).not.toMatch(/le plus complet/)
})



it('la version d’un groupe s’ouvre au clavier : c’est un bouton (Entrée et Espace), pas une zone cliquable', async () => {
  const user = userEvent.setup()
  render(<DedupPanel viewScope="project-1" embedded />, { wrapper: avecConfirmation })

  const version = await screen.findByRole('button', { name: /ancienne\.md/ })
  expect(version).toHaveClass('lib-main')
  version.focus()
  await user.keyboard('{Enter}')

  expect(await screen.findByRole('button', { name: /← Doublons/ })).toBeInTheDocument()   // le lecteur est ouvert
})
