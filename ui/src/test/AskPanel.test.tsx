import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import { ErreurAvecCode, ask, getTranscript } from '../api'
import { AskPanel } from '../components/AskPanel'


vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, ask: vi.fn(), getTranscript: vi.fn() }
})


beforeEach(() => {
  vi.mocked(ask).mockResolvedValue({ answer: 'Réponse', sources: [] })
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'meeting.md', meta: {}, body: '[00:12] Passage', content: '[00:12] Passage',
  })
})


it('affiche la portée projet et peut élargir explicitement à tout le corpus', async () => {
  const user = userEvent.setup()
  render(<AskPanel viewScope="project-id" scopeLabel="Client" />)

  expect(screen.getByText('Dans : Projet Client')).toBeInTheDocument()
  await user.type(screen.getByPlaceholderText('Poser une question sur vos transcripts…'), 'Question')
  await user.click(screen.getByRole('button', { name: 'Demander' }))
  expect(ask).toHaveBeenLastCalledWith(
    'Question', { mode: 'project', project_id: 'project-id' },
  )

  await user.click(screen.getByRole('button', { name: 'Chercher dans tous mes projets' }))
  expect(screen.getByText('Dans : Tous mes projets')).toBeInTheDocument()
  expect(screen.getByText(/Plus de contexte/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Demander' }))
  expect(ask).toHaveBeenLastCalledWith('Question', { mode: 'all' })
})


it('sépare les preuves et ouvre une citation à son horodatage', async () => {
  const user = userEvent.setup()
  vi.mocked(ask).mockResolvedValue({
    answer: 'Décision confirmée [1].',
    sources: [
      {
        n: 1, name: 'meeting.md', title: 'Comité', ts: '00:12', rang: 1,
        excerpt: 'Le comité confirme la décision.', document_id: 'doc-1',
        meeting_id: 'meeting-1', project_id: 'project-id', project: 'Client',
      },
      {
        n: 2, name: 'context.md', title: 'Contexte', ts: null, rang: 1,
        excerpt: 'Un passage retrouvé mais non cité.', document_id: 'doc-2',
        meeting_id: 'meeting-2', project_id: 'project-id', project: 'Client',
      },
    ],
  })
  render(<AskPanel viewScope="project-id" scopeLabel="Client" />)

  await user.type(screen.getByPlaceholderText('Poser une question sur vos transcripts…'), 'Décision ?')
  await user.click(screen.getByRole('button', { name: 'Demander' }))

  expect(await screen.findByRole('heading', { name: 'Réponse' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Passages retrouvés' })).toBeInTheDocument()
  expect(screen.getByText('Cité')).toBeInTheDocument()
  expect(screen.getByText('Contexte non cité')).toBeInTheDocument()
  expect(screen.getByText('À vérifier dans les sources')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Ouvrir la source 1' }))
  await waitFor(() => expect(getTranscript).toHaveBeenCalledWith('meeting.md', 'project-id'))
})


it('rend explicitement l’absence de preuve', async () => {
  const user = userEvent.setup()
  render(<AskPanel viewScope="project-id" scopeLabel="Client" />)

  await user.type(screen.getByPlaceholderText('Poser une question sur vos transcripts…'), 'Inconnu ?')
  await user.click(screen.getByRole('button', { name: 'Demander' }))

  expect(await screen.findByText('Aucune preuve retrouvée')).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Réponse' })).not.toBeInTheDocument()
})


it('signale une réponse dont les citations ne correspondent pas aux preuves', async () => {
  const user = userEvent.setup()
  vi.mocked(ask).mockResolvedValue({
    answer: 'Affirmation [2]',
    sources: [{
      n: 1, name: 'meeting.md', title: 'Comité', ts: null, rang: 1,
      excerpt: 'Extrait', document_id: 'doc-1', meeting_id: 'meeting-1',
      project_id: 'project-id', project: 'Client',
    }],
  })
  render(<AskPanel viewScope="project-id" scopeLabel="Client" />)

  await user.type(screen.getByPlaceholderText('Poser une question sur vos transcripts…'), 'Question')
  await user.click(screen.getByRole('button', { name: 'Demander' }))

  expect(await screen.findByText(/source est absente/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Ouvrir la source 2' })).not.toBeInTheDocument()
})


/**
 * L'état initial de l'Assistant.
 *
 * Ce qui est vérifié n'est pas le remplissage de l'écran : ce sont deux
 * renseignements que l'utilisateur n'obtenait qu'APRÈS avoir travaillé.
 */
it('annonce un modèle injoignable AVANT la première frappe', async () => {
  // Aujourd'hui l'échec arrivait après quarante mots tapés et vingt secondes
  // d'attente. Le dire d'emblée ne coûte rien : la configuration est déjà là.
  render(<AskPanel viewScope="p" scopeLabel="Client" llmAvailable={false} />)
  expect(await screen.findByText(/Aucun modèle local n’est disponible/)).toBeInTheDocument()
})

it('ne dit RIEN du modèle quand il est joignable', async () => {
  render(<AskPanel viewScope="p" scopeLabel="Client" llmAvailable />)
  expect(screen.queryByText(/Aucun modèle local n’est disponible/)).not.toBeInTheDocument()
})

it('distingue une portée VIDE d’un champ vide', async () => {
  // Sans ça, l'utilisateur pose une question à un corpus qui n'a rien à lire et
  // n'apprend qu'ensuite qu'il n'y avait rien.
  render(<AskPanel viewScope="p" scopeLabel="Client" scopeCount={0} />)
  expect(screen.getByText('Aucun document')).toBeInTheDocument()
  // Proposer des amorces sur un corpus vide serait une invitation à l'échec.
  expect(screen.queryByText('Pour commencer :')).not.toBeInTheDocument()
})

it('annonce ce qui alimente la réponse, accordé', async () => {
  const { rerender } = render(<AskPanel viewScope="p" scopeLabel="Client" scopeCount={7} />)
  expect(screen.getByText(/7 documents de Client alimentent la réponse/)).toBeInTheDocument()

  rerender(<AskPanel viewScope="p" scopeLabel="Client" scopeCount={1} />)
  expect(screen.getByText(/1 document de Client alimente la réponse/)).toBeInTheDocument()
})

it('se TAIT sur le compte tant que les projets ne sont pas chargés', async () => {
  // Un « 0 document » affiché avant le chargement serait faux, et ferait
  // renoncer avant même d'avoir cherché.
  render(<AskPanel viewScope="p" scopeLabel="Client" />)
  expect(screen.queryByText(/alimente/)).not.toBeInTheDocument()
  expect(screen.queryByText('Aucun document')).not.toBeInTheDocument()
})

it('une amorce REMPLIT le champ sans envoyer la question', async () => {
  const user = userEvent.setup()
  render(<AskPanel viewScope="p" scopeLabel="Client" scopeCount={3} />)

  const amorce = screen.getByRole('button', { name: 'Quelles décisions ont été prises ?' })
  await user.click(amorce)

  expect(screen.getByPlaceholderText('Poser une question sur vos transcripts…'))
    .toHaveValue('Quelles décisions ont été prises ?')
  // La question reste celle de l'utilisateur : rien n'est parti tout seul.
  expect(ask).not.toHaveBeenCalled()
})


/**
 * Même composition côté Assistant, avec une conséquence de plus :
 * l'état « portée vide » y masque aussi les amorces. Élargir la portée sans
 * mettre à jour l'état initial laissait donc un écran sans aucune entrée.
 */
it('n’annonce plus une portée VIDE quand la question a été élargie', async () => {
  const user = userEvent.setup()
  render(<AskPanel viewScope="p" scopeLabel="Client" scopeCount={0}
                   scopeCountAll={12} scopeLabelAll="Tous mes projets" />)

  expect(screen.getByText('Aucun document')).toBeInTheDocument()
  expect(screen.queryByText('Pour commencer :')).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /tous mes projets/i }))

  expect(screen.queryByText('Aucun document')).not.toBeInTheDocument()
  expect(screen.getByText(/12 documents de Tous mes projets alimentent la réponse/))
    .toBeInTheDocument()
  // Les amorces reviennent : il y a de nouveau quelque chose à interroger.
  expect(screen.getByText('Pour commencer :')).toBeInTheDocument()
})

it('un refus de l’Assistant à code connu dit la phrase du catalogue, jamais celle du serveur', async () => {
  const user = userEvent.setup()
  vi.mocked(ask).mockRejectedValue(new ErreurAvecCode('ask.unavailable', 'Assistant indisponible : phrase-du-serveur'))
  render(<AskPanel viewScope="project-id" scopeLabel="Client" />)
  await user.type(screen.getByPlaceholderText('Poser une question sur vos transcripts…'), 'Question')
  await user.click(screen.getByRole('button', { name: 'Demander' }))

  expect(await screen.findByText('L’Assistant est indisponible pour le moment : réessayez dans un moment.')).toBeInTheDocument()
  expect(document.body.textContent).not.toMatch(/phrase-du-serveur/)
})


it('une panne de l’Assistant sans code dit la phrase du contexte : ni « HTTP », ni le texte de l’exception', async () => {
  const user = userEvent.setup()
  vi.mocked(ask).mockRejectedValue(new Error('HTTP 500: {"detail":"assistant-brut"}'))
  render(<AskPanel viewScope="project-id" scopeLabel="Client" />)
  await user.type(screen.getByPlaceholderText('Poser une question sur vos transcripts…'), 'Question')
  await user.click(screen.getByRole('button', { name: 'Demander' }))

  expect(await screen.findByText('L’Assistant n’a pas pu répondre.')).toBeInTheDocument()
  expect(document.body.textContent).not.toMatch(/HTTP|assistant-brut/)
})


it('l’écran a un titre de niveau 1 (masqué), annonce l’attente puis l’échec', async () => {
  const user = userEvent.setup()
  vi.mocked(ask).mockReturnValueOnce(new Promise(() => {}))
  const { unmount } = render(<AskPanel viewScope="project-id" scopeLabel="Client" />)

  expect(screen.getByRole('heading', { level: 1, name: 'Assistant' })).toBeInTheDocument()
  await user.type(screen.getByPlaceholderText('Poser une question sur vos transcripts…'), 'Question')
  await user.click(screen.getByRole('button', { name: 'Demander' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Lecture des transcripts et rédaction de la réponse')
  unmount()

  vi.mocked(ask).mockRejectedValueOnce(new Error('HTTP 500: {"detail":"brut-assistant"}'))
  render(<AskPanel viewScope="project-id" scopeLabel="Client" />)
  await user.type(screen.getByPlaceholderText('Poser une question sur vos transcripts…'), 'Question')
  await user.click(screen.getByRole('button', { name: 'Demander' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('L’Assistant n’a pas pu répondre.')
  expect(document.body.textContent).not.toMatch(/HTTP|brut-assistant/)
})


it('une source ouverte garde le titre de niveau 1 de l’écran', async () => {
  const user = userEvent.setup()
  vi.mocked(ask).mockResolvedValue({
    answer: 'Décision confirmée [1].',
    sources: [{
      n: 1, name: 'meeting.md', title: 'Comité', ts: '00:12', rang: 1,
      excerpt: 'Le comité confirme la décision.', document_id: 'doc-1',
      meeting_id: 'meeting-1', project_id: 'project-id', project: 'Client',
    }],
  })
  render(<AskPanel viewScope="project-id" scopeLabel="Client" />)
  await user.type(screen.getByPlaceholderText('Poser une question sur vos transcripts…'), 'Décision ?')
  await user.click(screen.getByRole('button', { name: 'Demander' }))
  await user.click(await screen.findByRole('button', { name: 'Ouvrir la source 1' }))

  await waitFor(() => expect(getTranscript).toHaveBeenCalledWith('meeting.md', 'project-id'))
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Assistant')
})
