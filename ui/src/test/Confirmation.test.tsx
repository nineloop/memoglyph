import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { LangProvider } from '../components/LangProvider'
import { ConfirmationProvider, useConfirmer, type DemandeConfirmation } from '../design/Confirmation'

/** Un déclencheur ordinaire : il demande, puis dit ce qu'on lui a répondu. */
function Declencheur({ demande, nom = 'Déclencher' }: { demande: DemandeConfirmation; nom?: string }) {
  const confirmer = useConfirmer()
  const [reponse, setReponse] = useState('aucune')
  return (
    <>
      <button type="button" onClick={async () => setReponse(String(await confirmer(demande)))}>{nom}</button>
      <output aria-label="réponse">{reponse}</output>
    </>
  )
}

const DEMANDE: DemandeConfirmation = {
  titre: 'Annuler ce traitement ?',
  phrase: 'Le travail en cours sera perdu.',
  libelleAction: 'Annuler le traitement',
}

function monter(demande: DemandeConfirmation = DEMANDE, langue: 'fr' | 'en' = 'fr', extra?: React.ReactNode) {
  return render(
    <LangProvider initiale={langue}>
      <ConfirmationProvider>
        <Declencheur demande={demande} />
        {extra}
      </ConfirmationProvider>
    </LangProvider>,
  )
}

const reponse = () => screen.getByLabelText('réponse')
const declencheur = () => screen.getByRole('button', { name: 'Déclencher' })

async function ouvrir(user: ReturnType<typeof userEvent.setup>) {
  await user.click(declencheur())
  return screen.findByRole('dialog')
}

describe('la fenêtre de confirmation', () => {
  it('ne montre rien tant qu’on ne lui demande rien', () => {
    monter()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('s’ouvre en dialogue modal nommé par son titre, avec la phrase', async () => {
    const user = userEvent.setup()
    monter()
    const dialogue = await ouvrir(user)

    expect(dialogue).toHaveAttribute('aria-modal', 'true')
    expect(screen.getByRole('dialog', { name: 'Annuler ce traitement ?' })).toBe(dialogue)
    expect(within(dialogue).getByText('Le travail en cours sera perdu.')).toBeInTheDocument()
  })

  it('met le focus sur « Annuler », l’action la moins risquée', async () => {
    const user = userEvent.setup()
    monter()
    const dialogue = await ouvrir(user)
    expect(within(dialogue).getByRole('button', { name: 'Annuler' })).toHaveFocus()
  })

  it('nomme le bouton de confirmation par l’action, jamais « OK »', async () => {
    const user = userEvent.setup()
    monter()
    const dialogue = await ouvrir(user)
    expect(within(dialogue).getByRole('button', { name: 'Annuler le traitement' })).toBeInTheDocument()
    expect(within(dialogue).queryByRole('button', { name: /^OK$/i })).toBeNull()
  })

  it('confirmer rend true et ferme la fenêtre', async () => {
    const user = userEvent.setup()
    monter()
    const dialogue = await ouvrir(user)
    await user.click(within(dialogue).getByRole('button', { name: 'Annuler le traitement' }))

    expect(reponse()).toHaveTextContent('true')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('annuler rend false et ferme la fenêtre', async () => {
    const user = userEvent.setup()
    monter()
    const dialogue = await ouvrir(user)
    await user.click(within(dialogue).getByRole('button', { name: 'Annuler' }))

    expect(reponse()).toHaveTextContent('false')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('Échap ferme sans agir (false) et rend le focus au déclencheur', async () => {
    const user = userEvent.setup()
    monter()
    await ouvrir(user)
    await user.keyboard('{Escape}')

    expect(reponse()).toHaveTextContent('false')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(declencheur()).toHaveFocus()
  })

  it('rend aussi le focus au déclencheur après une confirmation', async () => {
    const user = userEvent.setup()
    monter()
    const dialogue = await ouvrir(user)
    await user.click(within(dialogue).getByRole('button', { name: 'Annuler le traitement' }))
    expect(declencheur()).toHaveFocus()
  })

  it('garde Tab dans la fenêtre, dans les deux sens', async () => {
    const user = userEvent.setup()
    monter()
    const dialogue = await ouvrir(user)
    const annuler = within(dialogue).getByRole('button', { name: 'Annuler' })
    const action = within(dialogue).getByRole('button', { name: 'Annuler le traitement' })

    expect(annuler).toHaveFocus()
    await user.tab()
    expect(action).toHaveFocus()
    await user.tab()
    expect(annuler).toHaveFocus() // reboucle au lieu de sortir vers la page
    await user.tab({ shift: true })
    expect(action).toHaveFocus() // et dans l'autre sens
  })

  it('une demande faite pendant qu’une fenêtre est ouverte ne s’empile pas et rend false', async () => {
    const user = userEvent.setup()
    function Double() {
      const confirmer = useConfirmer()
      const [seconde, setSeconde] = useState('aucune')
      return (
        <>
          <button type="button" onClick={() => {
            void confirmer(DEMANDE)
            void confirmer({ ...DEMANDE, titre: 'Seconde' }).then((r) => setSeconde(String(r)))
          }}>Double</button>
          <output aria-label="seconde">{seconde}</output>
        </>
      )
    }
    render(<LangProvider initiale="fr"><ConfirmationProvider><Double /></ConfirmationProvider></LangProvider>)
    await user.click(screen.getByRole('button', { name: 'Double' }))

    expect(await screen.findAllByRole('dialog')).toHaveLength(1)
    expect(screen.getByRole('dialog', { name: 'Annuler ce traitement ?' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Seconde' })).toBeNull()
    expect(screen.getByLabelText('seconde')).toHaveTextContent('false')
  })

  it('ne se superpose pas à un autre dialogue modal déjà ouvert : rend false, sans seconde fenêtre', async () => {
    const user = userEvent.setup()
    monter(DEMANDE, 'fr', <div role="dialog" aria-modal="true" aria-label="Autre fenêtre">autre</div>)
    await user.click(declencheur())

    expect(reponse()).toHaveTextContent('false')
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(screen.getByRole('dialog', { name: 'Autre fenêtre' })).toBeInTheDocument()
  })

  it('rend les lignes de détail en liste', async () => {
    const user = userEvent.setup()
    monter({ ...DEMANDE, details: ['Réunion A (2 documents)', 'Réunion B (3 documents)'] })
    const dialogue = await ouvrir(user)

    const liste = within(dialogue).getByRole('list')
    expect(within(liste).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Réunion A (2 documents)', 'Réunion B (3 documents)',
    ])
  })

  it('une liste de détails vide ne rend pas de liste', async () => {
    const user = userEvent.setup()
    monter({ ...DEMANDE, details: [] })
    const dialogue = await ouvrir(user)
    expect(within(dialogue).queryByRole('list')).toBeNull()
  })

  it('sans détail, ne rend aucune liste', async () => {
    const user = userEvent.setup()
    monter()
    const dialogue = await ouvrir(user)
    expect(within(dialogue).queryByRole('list')).toBeNull()
  })

  it('un nom de refus propre à la demande remplace « Annuler », et le focus y entre', async () => {
    const user = userEvent.setup()
    monter({ ...DEMANDE, libelleRefus: 'Garder le traitement' })
    const dialogue = await ouvrir(user)

    expect(within(dialogue).getByRole('button', { name: 'Garder le traitement' })).toHaveFocus()
    expect(within(dialogue).queryByRole('button', { name: 'Annuler' })).toBeNull()
  })

  it('en anglais, le bouton de refus est en anglais', async () => {
    const user = userEvent.setup()
    monter({ titre: 'Cancel this job?', phrase: 'Work in progress will be lost.', libelleAction: 'Cancel the job' }, 'en')
    const dialogue = await ouvrir(user)

    expect(within(dialogue).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    expect(within(dialogue).getByRole('button', { name: 'Cancel the job' })).toBeInTheDocument()
    expect(within(dialogue).queryByRole('button', { name: 'Annuler' })).toBeNull()
  })
})
