import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import { changerActivation, creerCompte, getEtatAuth, listerComptes } from '../api'
import { AuthGate } from '../components/AuthGate'
import { BoutonComptes, ComptesPanel } from '../components/ComptesPanel'
import { LangProvider } from '../components/LangProvider'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getEtatAuth: vi.fn(),
    listerComptes: vi.fn(),
    creerCompte: vi.fn(),
    changerActivation: vi.fn(),
  }
})

const COMPTES = [
  { identifiant: 'admin', admin: true, actif: true, cree_le: '2026-10-19' },
  { identifiant: 'alice', admin: false, actif: false, cree_le: '2026-10-19' },
]

beforeEach(() => {
  vi.mocked(listerComptes).mockReset().mockResolvedValue(COMPTES)
  vi.mocked(creerCompte).mockReset()
  vi.mocked(changerActivation).mockReset().mockResolvedValue(undefined)
})

function sousSession(admin: boolean) {
  vi.mocked(getEtatAuth).mockResolvedValue({
    installation_requise: false, connecte: true, utilisateur: { identifiant: 'u', admin },
  })
  return render(<LangProvider><AuthGate><BoutonComptes /></AuthGate></LangProvider>)
}

it('le bouton « Comptes » n’existe que pour un administrateur', async () => {
  const membre = sousSession(false)
  await waitFor(() => expect(getEtatAuth).toHaveBeenCalled())
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.queryByRole('button', { name: 'Comptes' })).not.toBeInTheDocument()
  membre.unmount()

  const user = userEvent.setup()
  sousSession(true)
  await user.click(await screen.findByRole('button', { name: 'Comptes' }))
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(await screen.findByText('alice')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Fermer' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('liste les comptes avec rôle et état, et bascule l’activation', async () => {
  const user = userEvent.setup()
  render(<LangProvider><ComptesPanel /></LangProvider>)

  const ligneAlice = (await screen.findByText('alice')).closest('tr')!
  expect(within(ligneAlice).getByText('Membre')).toBeInTheDocument()
  expect(within(ligneAlice).getByText('Désactivé')).toBeInTheDocument()
  const ligneAdmin = screen.getByText('admin').closest('tr')!
  expect(within(ligneAdmin).getByText('Administrateur')).toBeInTheDocument()

  await user.click(within(ligneAlice).getByRole('button', { name: 'Réactiver' }))
  expect(changerActivation).toHaveBeenCalledWith('alice', true)
  await user.click(within(ligneAdmin).getByRole('button', { name: 'Désactiver' }))
  expect(changerActivation).toHaveBeenCalledWith('admin', false)
})

it('affiche le refus du serveur, par exemple pour le dernier administrateur', async () => {
  const user = userEvent.setup()
  vi.mocked(changerActivation).mockRejectedValue(
    new Error('Impossible de désactiver le dernier administrateur actif.'))
  render(<LangProvider><ComptesPanel /></LangProvider>)

  const ligneAdmin = (await screen.findByText('admin')).closest('tr')!
  await user.click(within(ligneAdmin).getByRole('button', { name: 'Désactiver' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('dernier administrateur actif')
})

it('crée un compte, administrateur ou non', async () => {
  const user = userEvent.setup()
  vi.mocked(creerCompte).mockResolvedValue({ utilisateur: { identifiant: 'bob', admin: true } })
  render(<LangProvider><ComptesPanel /></LangProvider>)
  await screen.findByText('alice')

  await user.type(screen.getByLabelText('Identifiant'), 'bob')
  await user.type(screen.getByLabelText('Mot de passe'), 'un mot de passe solide')
  await user.click(screen.getByRole('checkbox', { name: 'Administrateur' }))
  await user.click(screen.getByRole('button', { name: 'Créer le compte' }))

  expect(creerCompte).toHaveBeenCalledWith('bob', 'un mot de passe solide', true)
  expect(await screen.findByRole('status')).toHaveTextContent('Compte « bob » créé.')
  expect(listerComptes).toHaveBeenCalledTimes(2)
})
