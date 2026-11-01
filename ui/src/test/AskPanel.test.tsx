import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import { ask, getTranscript } from '../api'
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

  expect(screen.getByText('Portée : Projet Client')).toBeInTheDocument()
  await user.type(screen.getByPlaceholderText('Pose une question sur tes transcripts…'), 'Question')
  await user.click(screen.getByRole('button', { name: 'Demander' }))
  expect(ask).toHaveBeenLastCalledWith(
    'Question', { mode: 'project', project_id: 'project-id' },
  )

  await user.click(screen.getByRole('button', { name: 'Chercher dans tous mes projets' }))
  expect(screen.getByText('Portée : Tous mes projets')).toBeInTheDocument()
  expect(screen.getByText(/davantage de contexte/)).toBeInTheDocument()
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

  await user.type(screen.getByPlaceholderText('Pose une question sur tes transcripts…'), 'Décision ?')
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

  await user.type(screen.getByPlaceholderText('Pose une question sur tes transcripts…'), 'Inconnu ?')
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

  await user.type(screen.getByPlaceholderText('Pose une question sur tes transcripts…'), 'Question')
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
  expect(await screen.findByText(/Aucun modèle local joignable/)).toBeInTheDocument()
})

it('ne dit RIEN du modèle quand il est joignable', async () => {
  render(<AskPanel viewScope="p" scopeLabel="Client" llmAvailable />)
  expect(screen.queryByText(/Aucun modèle local joignable/)).not.toBeInTheDocument()
})

it('distingue une portée VIDE d’un champ vide', async () => {
  // Sans ça, l'utilisateur pose une question à un corpus qui n'a rien à lire et
  // n'apprend qu'ensuite qu'il n'y avait rien.
  render(<AskPanel viewScope="p" scopeLabel="Client" scopeCount={0} />)
  expect(screen.getByText('Cette portée ne contient aucun document')).toBeInTheDocument()
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
  expect(screen.queryByText('Cette portée ne contient aucun document')).not.toBeInTheDocument()
})

it('une amorce REMPLIT le champ sans envoyer la question', async () => {
  const user = userEvent.setup()
  render(<AskPanel viewScope="p" scopeLabel="Client" scopeCount={3} />)

  const amorce = screen.getByRole('button', { name: 'Quelles décisions ont été prises ?' })
  await user.click(amorce)

  expect(screen.getByPlaceholderText('Pose une question sur tes transcripts…'))
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

  expect(screen.getByText('Cette portée ne contient aucun document')).toBeInTheDocument()
  expect(screen.queryByText('Pour commencer :')).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /tous mes projets/i }))

  expect(screen.queryByText('Cette portée ne contient aucun document')).not.toBeInTheDocument()
  expect(screen.getByText(/12 documents de Tous mes projets alimentent la réponse/))
    .toBeInTheDocument()
  // Les amorces reviennent : il y a de nouveau quelque chose à interroger.
  expect(screen.getByText('Pour commencer :')).toBeInTheDocument()
})
