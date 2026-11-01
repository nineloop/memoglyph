import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { connecter, deconnecter, getEtatAuth, installer } from '../api'
import { AuthGate, BoutonDeconnexion } from '../components/AuthGate'
import { LangProvider } from '../components/LangProvider'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getEtatAuth: vi.fn(),
    installer: vi.fn(),
    connecter: vi.fn(),
    deconnecter: vi.fn(),
  }
})

const fetchOrigine = window.fetch

beforeEach(() => {
  vi.mocked(getEtatAuth).mockReset()
  vi.mocked(installer).mockReset()
  vi.mocked(connecter).mockReset()
  vi.mocked(deconnecter).mockReset().mockResolvedValue(undefined)
})

afterEach(() => { window.fetch = fetchOrigine })

function rendre() {
  return render(
    <LangProvider>
      <AuthGate>
        <p>Application</p>
        <BoutonDeconnexion />
      </AuthGate>
    </LangProvider>,
  )
}

it('premier lancement : seul l’écran « Créer le compte administrateur » s’affiche', async () => {
  const user = userEvent.setup()
  vi.mocked(getEtatAuth).mockResolvedValue({ installation_requise: true, connecte: false, utilisateur: null })
  vi.mocked(installer).mockResolvedValue({ utilisateur: { identifiant: 'admin', admin: true } })
  rendre()

  expect(await screen.findByRole('heading', { name: 'Créer le compte administrateur' })).toBeInTheDocument()
  expect(screen.queryByText('Application')).not.toBeInTheDocument()

  await user.type(screen.getByLabelText('Code d’installation'), 'ABCD-EFGH-JKMN')
  await user.type(screen.getByLabelText('Identifiant'), 'admin')
  await user.type(screen.getByLabelText('Mot de passe'), 'un mot de passe solide')
  await user.type(screen.getByLabelText('Confirmer le mot de passe'), 'autre chose encore')
  await user.click(screen.getByRole('button', { name: 'Créer le compte' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Les deux mots de passe diffèrent.')
  expect(installer).not.toHaveBeenCalled()

  await user.clear(screen.getByLabelText('Confirmer le mot de passe'))
  await user.type(screen.getByLabelText('Confirmer le mot de passe'), 'un mot de passe solide')
  await user.click(screen.getByRole('button', { name: 'Créer le compte' }))

  expect(installer).toHaveBeenCalledWith('ABCD-EFGH-JKMN', 'admin', 'un mot de passe solide')
  expect(await screen.findByText('Application')).toBeInTheDocument()
  // L'écran d'installation a disparu avec le compte créé.
  expect(screen.queryByRole('heading', { name: 'Créer le compte administrateur' })).not.toBeInTheDocument()
})

it('sans session : l’écran de connexion, et le refus du backend affiché', async () => {
  const user = userEvent.setup()
  vi.mocked(getEtatAuth).mockResolvedValue({ installation_requise: false, connecte: false, utilisateur: null })
  vi.mocked(connecter)
    .mockRejectedValueOnce(new Error('Identifiant ou mot de passe incorrect.'))
    .mockResolvedValueOnce({ utilisateur: { identifiant: 'alice', admin: false } })
  rendre()

  expect(await screen.findByRole('heading', { name: 'Connexion à Memoglyph' })).toBeInTheDocument()
  expect(screen.queryByText('Application')).not.toBeInTheDocument()
  await user.type(screen.getByLabelText('Identifiant'), 'alice')
  await user.type(screen.getByLabelText('Mot de passe'), 'mauvais')
  await user.click(screen.getByRole('button', { name: 'Se connecter' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Identifiant ou mot de passe incorrect.')

  await user.click(screen.getByRole('button', { name: 'Se connecter' }))
  expect(await screen.findByText('Application')).toBeInTheDocument()
})

it('avec une session : l’application, puis la déconnexion ramène à l’écran de connexion', async () => {
  const user = userEvent.setup()
  vi.mocked(getEtatAuth).mockResolvedValue({
    installation_requise: false, connecte: true, utilisateur: { identifiant: 'alice', admin: false },
  })
  rendre()

  expect(await screen.findByText('Application')).toBeInTheDocument()
  const bouton = screen.getByRole('button', { name: 'Se déconnecter' })
  expect(bouton).toHaveAttribute('title', 'Connecté en tant que alice')
  await user.click(bouton)

  expect(deconnecter).toHaveBeenCalled()
  expect(await screen.findByRole('heading', { name: 'Connexion à Memoglyph' })).toBeInTheDocument()
})

it('une réponse 401 de l’API ramène à l’écran de connexion, avec un message d’expiration', async () => {
  vi.mocked(getEtatAuth).mockResolvedValue({
    installation_requise: false, connecte: true, utilisateur: { identifiant: 'alice', admin: false },
  })
  window.fetch = vi.fn().mockResolvedValue(new Response('{}', { status: 401 }))
  rendre()
  expect(await screen.findByText('Application')).toBeInTheDocument()

  await window.fetch('/api/config')

  expect(await screen.findByText('Votre session a expiré. Reconnectez-vous.')).toBeInTheDocument()
  expect(screen.queryByText('Application')).not.toBeInTheDocument()
})

it('un 401 des routes d’authentification elles-mêmes ne déclenche rien', async () => {
  vi.mocked(getEtatAuth).mockResolvedValue({
    installation_requise: false, connecte: true, utilisateur: { identifiant: 'alice', admin: false },
  })
  window.fetch = vi.fn().mockResolvedValue(new Response('{}', { status: 401 }))
  rendre()
  expect(await screen.findByText('Application')).toBeInTheDocument()

  await window.fetch('/api/auth/connexion')

  expect(screen.getByText('Application')).toBeInTheDocument()
})

it('backend injoignable : l’application s’affiche et dira elle-même la panne', async () => {
  vi.mocked(getEtatAuth).mockRejectedValue(new Error('réseau'))
  rendre()

  await waitFor(() => expect(screen.getByText('Application')).toBeInTheDocument())
  // Sans session connue, aucun bouton de déconnexion.
  expect(screen.queryByRole('button', { name: 'Se déconnecter' })).not.toBeInTheDocument()
})
