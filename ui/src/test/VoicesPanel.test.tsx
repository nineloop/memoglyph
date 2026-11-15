import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import type { Voice } from '../api'
import { getVoices, mergeVoices, renameVoice } from '../api'
import { LangProvider } from '../components/LangProvider'
import { VoicesPanel } from '../components/VoicesPanel'


vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getVoices: vi.fn(),
    renameVoice: vi.fn(),
    mergeVoices: vi.fn(),
  }
})


const voices: Voice[] = [
  {
    id: 1, label: 'Alice', label_source: 'manual', suggestion: null,
    display: 'Alice', n_members: 1,
    occurrences: [{
      name: 'comite.md', document_id: 'doc-1', title: 'Comité',
      speaker_local: 'Speaker 1', project_id: 'project-1', project: 'Client',
    }],
  },
  {
    id: 2, label: null, label_source: null, suggestion: 'Bob',
    display: 'Voix 2', n_members: 1,
    occurrences: [{
      name: 'entretien.md', document_id: 'doc-2', title: 'Entretien',
      speaker_local: 'Speaker 2', project_id: 'project-1', project: 'Client',
    }],
  },
]


beforeEach(() => {
  vi.mocked(getVoices).mockResolvedValue(voices)
  vi.mocked(renameVoice).mockResolvedValue({ id: 2, label: 'Bob' })
  vi.mocked(mergeVoices).mockResolvedValue({ merged: 1, into: 2 })
})


it('sépare le nom confirmé de la suggestion LLM et affiche la provenance projet', async () => {
  const user = userEvent.setup()
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)

  expect(await screen.findByRole('heading', { name: 'Alice' })).toBeInTheDocument()
  expect(screen.getByText('Dans : Projet Client')).toBeInTheDocument()
  expect(screen.getByText('Saisi et validé manuellement')).toBeInTheDocument()
  expect(screen.queryByText('Projet inconnu')).not.toBeInTheDocument()
  expect(screen.getAllByText('Client')).not.toHaveLength(0)
  expect(getVoices).toHaveBeenCalledWith('project-1')

  // Un seul détail à la fois. La suggestion de la voix 2 se lit après
  // l'avoir choisie dans le registre.
  await user.click(within(screen.getByLabelText('Registre des voix'))
    .getByRole('button', { name: /Voix 2/ }))
  expect(screen.getByText('Suggestion du modèle')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: 'Confirmer « Bob »' }))
  await waitFor(() => expect(renameVoice).toHaveBeenCalledWith(2, 'Bob', 'project-1'))
})


it('fusionne depuis un dialogue comparatif en conservant la portée', async () => {
  const user = userEvent.setup()
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)
  await screen.findByRole('heading', { name: 'Alice' })

  // La source de la fusion est la voix SÉLECTIONNÉE — ici la première, par
  // repli. La cible reste choisie dans le dialogue.
  await user.click(screen.getByRole('button', { name: 'Comparer et fusionner' }))
  const dialog = screen.getByRole('dialog', { name: 'Comparer deux voix' })
  expect(within(dialog).getByText('Disparaîtra')).toBeInTheDocument()
  expect(within(dialog).getByText('Sera conservée')).toBeInTheDocument()
  expect(within(dialog).getByText('Alice')).toBeInTheDocument()
  expect(within(dialog).getByText('Voix 2')).toBeInTheDocument()

  await user.click(within(dialog).getByRole('button', { name: 'Confirmer la fusion' }))
  await waitFor(() => expect(mergeVoices).toHaveBeenCalledWith(1, 2, 'project-1'))
})


/**
 * Le registre en master-detail.
 *
 * Le gain de densité n'est pas l'enjeu du test : c'est ce qu'il ne doit pas
 * coûter. Un registre qui montre tout n'a pas de sélection à perdre ; un
 * master-detail en a une, et elle peut disparaître sous les pieds.
 */
it('n’ouvre qu’UN détail à la fois, et le dit dans le registre', async () => {
  const user = userEvent.setup()
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)
  await screen.findByRole('heading', { name: 'Alice' })

  const registre = screen.getByLabelText('Registre des voix')
  expect(within(registre).getAllByRole('button')).toHaveLength(2)
  expect(within(registre).getByRole('button', { name: /Alice/ })).toHaveAttribute('aria-current', 'true')
  expect(within(registre).getByRole('button', { name: /Voix 2/ })).not.toHaveAttribute('aria-current')
  // Le détail d'Alice, et lui seul.
  expect(screen.getByText('Saisi et validé manuellement')).toBeInTheDocument()
  expect(screen.queryByText('Suggestion du modèle')).not.toBeInTheDocument()

  await user.click(within(registre).getByRole('button', { name: /Voix 2/ }))

  expect(within(registre).getByRole('button', { name: /Voix 2/ })).toHaveAttribute('aria-current', 'true')
  expect(within(registre).getByRole('button', { name: /Alice/ })).not.toHaveAttribute('aria-current')
  expect(screen.getByText('Suggestion du modèle')).toBeInTheDocument()
  expect(screen.queryByText('Saisi et validé manuellement')).not.toBeInTheDocument()
})

it('garde une ligne LISIBLE sans déplier : nom, état, occurrences', async () => {
  // Si la ligne maître ne portait que le nom, il faudrait ouvrir chaque voix
  // pour savoir laquelle est confirmée — le défaut qu'on vient de corriger,
  // déplacé d'un cran.
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)
  await screen.findByRole('heading', { name: 'Alice' })

  const registre = screen.getByLabelText('Registre des voix')
  expect(within(registre).getByRole('button', { name: /Alice/ })).toHaveTextContent('Nom confirmé')
  expect(within(registre).getByRole('button', { name: /Voix 2/ })).toHaveTextContent('Suggestion : Bob')
  expect(within(registre).getByRole('button', { name: /Voix 2/ })).toHaveTextContent('1')
})

it('RETOMBE sur une voix existante quand la sélection disparaît', async () => {
  // C'est exactement ce que fait une fusion réussie : la voix source quitte le
  // registre. Sans repli, le détail se viderait juste après l'action.
  const user = userEvent.setup()
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)
  await screen.findByRole('heading', { name: 'Alice' })

  const registre = screen.getByLabelText('Registre des voix')
  await user.click(within(registre).getByRole('button', { name: /Voix 2/ }))
  expect(screen.getByRole('heading', { name: 'Voix 2' })).toBeInTheDocument()

  // La voix 2 est fusionnée dans la 1 : le rechargement ne la rend plus.
  vi.mocked(getVoices).mockResolvedValue([voices[0]])
  await user.click(screen.getByRole('button', { name: /Rafraîchir/ }))

  expect(await screen.findByRole('heading', { name: 'Alice' })).toBeInTheDocument()
  expect(within(screen.getByLabelText('Registre des voix'))
    .getByRole('button', { name: /Alice/ })).toHaveAttribute('aria-current', 'true')
})

it('n’imbrique pas un titre dans un bouton', async () => {
  // `<button><h2>` est du HTML invalide : un bouton n'accepte que du contenu
  // de phrasé. Le titre vit donc dans le détail, jamais dans la ligne.
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)
  await screen.findByRole('heading', { name: 'Alice' })
  expect(document.querySelectorAll('button h1, button h2, button h3')).toHaveLength(0)
})


it('nomme une occurrence par le titre lisible de sa réunion, jamais par son slug', async () => {
  vi.mocked(getVoices).mockResolvedValue([{
    ...voices[0],
    occurrences: [{
      name: 'ami-is1009a-reunion.md', document_id: 'doc-3', title: 'ami-is1009a-reunion',
      source: 'AMI IS1009a, réunion.wav',
      speaker_local: 'Speaker 1', project_id: 'project-1', project: 'Client',
    }],
  }])
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)

  expect(await screen.findByText('AMI IS1009a, réunion')).toBeInTheDocument()
  expect(screen.queryByText('ami-is1009a-reunion')).not.toBeInTheDocument()
})


const PHRASE_ECHEC = 'Le registre des voix n’a pas pu être chargé.'
const ECHEC_SERVEUR = new Error('HTTP 503: {"detail":"base indisponible"}')

/** Une promesse qu'on résout ou qu'on rejette à la main, pour tenir un chargement « en cours ». */
function enAttente<T>() {
  let resoudre!: (v: T) => void
  let rejeter!: (e: unknown) => void
  const promesse = new Promise<T>((ok, ko) => { resoudre = ok; rejeter = ko })
  return { promesse, resoudre, rejeter }
}


it('un chargement qui échoue le dit et propose « Réessayer » : jamais « Aucune voix », jamais le texte de l’exception', async () => {
  vi.mocked(getVoices).mockRejectedValue(ECHEC_SERVEUR)
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)

  expect(await screen.findByRole('alert')).toHaveTextContent(PHRASE_ECHEC)
  expect(screen.getByRole('button', { name: 'Réessayer' })).toBeInTheDocument()
  expect(screen.queryByText('Aucune voix')).toBeNull()
  expect(document.body.textContent).not.toMatch(/HTTP|base indisponible/)
})


it('témoin : un registre réellement vide dit « Aucune voix », sans alerte', async () => {
  vi.mocked(getVoices).mockResolvedValue([])
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)

  expect(await screen.findByText('Aucune voix')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
})


it('un chargement lent n’affiche ni l’échec ni « Aucune voix » pendant l’attente', async () => {
  const lent = enAttente<Voice[]>()
  vi.mocked(getVoices).mockReturnValue(lent.promesse)
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)

  expect(screen.getByText('Chargement du registre…')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByText('Aucune voix')).toBeNull()

  lent.resoudre(voices)
  expect(await screen.findByRole('heading', { name: 'Alice' })).toBeInTheDocument()
})


it('« Réessayer » repasse par l’attente, efface l’échec et affiche le registre quand le chargement réussit', async () => {
  const user = userEvent.setup()
  const seconde = enAttente<Voice[]>()
  vi.mocked(getVoices).mockRejectedValueOnce(ECHEC_SERVEUR).mockReturnValueOnce(seconde.promesse)
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)

  await user.click(await screen.findByRole('button', { name: 'Réessayer' }))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText('Chargement du registre…')).toBeInTheDocument()

  seconde.resoudre(voices)
  expect(await screen.findByRole('heading', { name: 'Alice' })).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
})


it('un second échec qui suit le « Réessayer » de la personne redonne le focus au bouton', async () => {
  const user = userEvent.setup()
  vi.mocked(getVoices).mockRejectedValue(ECHEC_SERVEUR)
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)

  await user.click(await screen.findByRole('button', { name: 'Réessayer' }))
  await waitFor(() => expect(getVoices).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Réessayer' })).toHaveFocus())
})


it('un échec qui suit un registre déjà affiché le remplace : on ne renomme ni ne fusionne depuis un registre périmé', async () => {
  const user = userEvent.setup()
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)
  expect(await screen.findByRole('heading', { name: 'Alice' })).toBeInTheDocument()

  vi.mocked(getVoices).mockRejectedValueOnce(ECHEC_SERVEUR)
  await user.click(screen.getByRole('button', { name: /Rafraîchir/ }))

  expect(await screen.findByRole('alert')).toHaveTextContent(PHRASE_ECHEC)
  expect(screen.queryByRole('heading', { name: 'Alice' })).toBeNull()
})


it('l’échec d’un chargement périmé (la portée a changé entre-temps) ne remplace pas la réponse la plus récente', async () => {
  const ancien = enAttente<Voice[]>()
  vi.mocked(getVoices).mockReturnValueOnce(ancien.promesse).mockResolvedValueOnce(voices)
  const { rerender } = render(<VoicesPanel viewScope="projet-a" scopeLabel="A" />)
  rerender(<VoicesPanel viewScope="projet-b" scopeLabel="B" />)

  expect(await screen.findByRole('heading', { name: 'Alice' })).toBeInTheDocument()
  ancien.rejeter(ECHEC_SERVEUR)
  await new Promise((fin) => setTimeout(fin, 20))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByRole('heading', { name: 'Alice' })).toBeInTheDocument()
})


it('en anglais, la phrase d’échec et l’action de reprise sont en anglais', async () => {
  vi.mocked(getVoices).mockRejectedValue(ECHEC_SERVEUR)
  render(<LangProvider initiale="en"><VoicesPanel viewScope="project-1" scopeLabel="Client" /></LangProvider>)

  expect(await screen.findByRole('alert')).toHaveTextContent('The voice registry could not be loaded.')
  expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
})


it('l’échec d’un renommage dit la phrase du contexte : ni « HTTP », ni le texte de l’exception', async () => {
  const user = userEvent.setup()
  vi.mocked(renameVoice).mockRejectedValue(new Error('HTTP 500: renommage-brut'))
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)
  await screen.findByRole('heading', { name: 'Alice' })
  await user.click(within(screen.getByLabelText('Registre des voix')).getByRole('button', { name: /Voix 2/ }))

  await user.click(screen.getByRole('button', { name: 'Confirmer « Bob »' }))

  expect(await screen.findByText('Le nom de la voix n’a pas pu être enregistré.')).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('Le nom de la voix n’a pas pu être enregistré.')   // annoncée
  expect(document.body.textContent).not.toMatch(/HTTP|renommage-brut/)
})


it('l’échec d’une fusion dit la phrase du contexte : ni « HTTP », ni le texte de l’exception', async () => {
  const user = userEvent.setup()
  vi.mocked(mergeVoices).mockRejectedValue(new Error('HTTP 500: fusion-brute'))
  render(<VoicesPanel viewScope="project-1" scopeLabel="Client" />)
  await screen.findByRole('heading', { name: 'Alice' })

  await user.click(screen.getByRole('button', { name: 'Comparer et fusionner' }))
  const dialog = screen.getByRole('dialog', { name: 'Comparer deux voix' })
  await user.click(within(dialog).getByRole('button', { name: 'Confirmer la fusion' }))

  expect(await screen.findByText('La fusion des voix n’a pas abouti.')).toBeInTheDocument()
  expect(document.body.textContent).not.toMatch(/HTTP|fusion-brute/)
})
