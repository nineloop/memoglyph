import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { beforeEach, expect, it, vi } from 'vitest'

import {
  ErreurAvecCode, changerActivation, connecter, creerCompte, creerJeton, creerPartage, getCloudUltra, getEtatAuth,
  getPartages, installer, listerComptes, listerJetons, retirerJeton, retirerPartage, setCloudUltra,
} from '../api'
import { AppareilsPanel } from '../components/AppareilsPanel'
import { AuthGate } from '../components/AuthGate'
import { ComptesPanel } from '../components/ComptesPanel'
import { ConfidentialitePanel } from '../components/ConfidentialitePanel'
import { LangProvider } from '../components/LangProvider'
import { PartageDialog } from '../components/PartageDialog'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    changerActivation: vi.fn(), connecter: vi.fn(), creerCompte: vi.fn(), creerJeton: vi.fn(), creerPartage: vi.fn(),
    getCloudUltra: vi.fn(), getEtatAuth: vi.fn(), getPartages: vi.fn(), installer: vi.fn(), listerComptes: vi.fn(),
    listerJetons: vi.fn(), retirerJeton: vi.fn(), retirerPartage: vi.fn(), setCloudUltra: vi.fn(),
  }
})

type Utilisateur = ReturnType<typeof userEvent.setup>
type Cas = {
  nom: string
  monter: () => ReactNode
  programmer: (erreur: unknown) => void
  agir: (user: Utilisateur, conteneur: HTMLElement) => Promise<void>
  repli: { fr: string; en: string }
  /** Un refus à code, avec la phrase du catalogue qu'il doit donner dans chaque langue. */
  code?: { code: string; fr: string; en: string }
}

const BRUT = new Error('HTTP 500: {"detail":"base indisponible"}')
const COMPTE = { identifiant: 'bruno', admin: false, actif: true, cree_le: '2026-10-21T10:00:00+00:00' }
const MAC = { id: 'J-1', nom: 'MacBook', cree_le: '2026-10-21T10:00:00+00:00', dernier_usage: null }
const PARTAGE = { id: 'P-1', proprietaire: 'alice', invite: 'bruno', type: 'projet', cible: 'p1', cree_le: '2026-10-21T10:00:00+00:00' }
const ADMIN = { installation_requise: false, connecte: true, utilisateur: { identifiant: 'alice', admin: true } }
const DECONNECTE = { installation_requise: false, connecte: false, utilisateur: null }

beforeEach(() => {
  for (const f of [changerActivation, connecter, creerCompte, creerJeton, creerPartage, getCloudUltra, getEtatAuth, getPartages,
    installer, listerComptes, listerJetons, retirerJeton, retirerPartage, setCloudUltra]) vi.mocked(f).mockReset()
  vi.mocked(getEtatAuth).mockResolvedValue(ADMIN)
  vi.mocked(listerJetons).mockResolvedValue([MAC])
  vi.mocked(listerComptes).mockResolvedValue([COMPTE])
  vi.mocked(getCloudUltra).mockResolvedValue({ autorise: false, modifie_le: null })
  vi.mocked(getPartages).mockResolvedValue({ donnes: [PARTAGE], recus: [] } as never)
})

/** Remplit les champs de texte d'un formulaire puis l'envoie. */
async function remplirEtEnvoyer(user: Utilisateur, conteneur: HTMLElement) {
  await waitFor(() => expect(conteneur.querySelector('button[type=submit]')).not.toBeNull())
  for (const champ of conteneur.querySelectorAll('input')) {
    if (champ.type === 'checkbox' || champ.readOnly) continue
    await user.type(champ, 'valeur-de-test')
  }
  await user.click(conteneur.querySelector('button[type=submit]') as HTMLElement)
}

const CAS: Cas[] = [
  {
    nom: 'la connexion',
    monter: () => <AuthGate><span /></AuthGate>,
    programmer: (e) => { vi.mocked(getEtatAuth).mockResolvedValue(DECONNECTE); vi.mocked(connecter).mockRejectedValue(e) },
    agir: remplirEtEnvoyer,
    repli: { fr: 'La connexion n’a pas abouti.', en: 'Sign-in did not complete.' },
    code: { code: 'auth.login.invalid', fr: 'Identifiant ou mot de passe incorrect.', en: 'Incorrect username or password.' },
  },
  {
    nom: 'l’installation',
    monter: () => <AuthGate><span /></AuthGate>,
    programmer: (e) => {
      vi.mocked(getEtatAuth).mockResolvedValue({ installation_requise: true, connecte: false, utilisateur: null })
      vi.mocked(installer).mockRejectedValue(e)
    },
    agir: remplirEtEnvoyer,
    repli: { fr: 'L’installation n’a pas abouti.', en: 'Installation did not complete.' },
    code: { code: 'setup.code.invalid', fr: 'Le code d’installation n’est pas valide.', en: 'The installation code is not valid.' },
  },
  {
    nom: 'le chargement des comptes',
    monter: () => <AuthGate><ComptesPanel /></AuthGate>,
    programmer: (e) => { vi.mocked(listerComptes).mockRejectedValue(e) },
    agir: async () => {},
    repli: { fr: 'Les comptes n’ont pas pu être chargés.', en: 'Accounts could not be loaded.' },
  },
  {
    nom: 'la création d’un compte',
    monter: () => <AuthGate><ComptesPanel /></AuthGate>,
    programmer: (e) => { vi.mocked(creerCompte).mockRejectedValue(e) },
    agir: async (user, c) => { await screen.findByText('bruno'); await remplirEtEnvoyer(user, c) },
    repli: { fr: 'Le compte n’a pas pu être créé.', en: 'The account could not be created.' },
    code: { code: 'account.id.taken', fr: 'Cet identifiant est déjà pris.', en: 'This username is already taken.' },
  },
  {
    nom: 'le changement d’état d’un compte',
    monter: () => <AuthGate><ComptesPanel /></AuthGate>,
    programmer: (e) => { vi.mocked(changerActivation).mockRejectedValue(e) },
    agir: async (user, c) => { await screen.findByText('bruno'); await user.click(c.querySelector('tbody button.chip') as HTMLElement) },
    repli: { fr: 'Le changement d’état du compte n’a pas abouti.', en: 'The account status change did not complete.' },
    code: { code: 'account.lastAdmin', fr: 'Le dernier administrateur actif ne peut pas être désactivé.', en: 'The last active administrator cannot be deactivated.' },
  },
  {
    nom: 'le chargement des appareils',
    monter: () => <AppareilsPanel />,
    programmer: (e) => { vi.mocked(listerJetons).mockRejectedValue(e) },
    agir: async () => {},
    repli: { fr: 'Les appareils n’ont pas pu être chargés.', en: 'Devices could not be loaded.' },
  },
  {
    nom: 'l’ajout d’un appareil',
    monter: () => <AppareilsPanel />,
    programmer: (e) => { vi.mocked(creerJeton).mockRejectedValue(e) },
    agir: async (user, c) => { await screen.findByText('MacBook'); await remplirEtEnvoyer(user, c) },
    repli: { fr: 'L’appareil n’a pas pu être ajouté.', en: 'The device could not be added.' },
    code: { code: 'device.name.invalid', fr: 'Ce nom d’appareil n’est pas valide : donnez-lui un nom court.', en: 'This device name is not valid: give it a short name.' },
  },
  {
    nom: 'le retrait d’un appareil',
    monter: () => <AppareilsPanel />,
    programmer: (e) => { vi.mocked(retirerJeton).mockRejectedValue(e) },
    agir: async (user, c) => { await screen.findByText('MacBook'); await user.click(c.querySelector('tbody button.chip') as HTMLElement) },
    repli: { fr: 'L’appareil n’a pas pu être retiré.', en: 'The device could not be removed.' },
    code: { code: 'device.unknown', fr: 'Cet appareil n’existe plus.', en: 'This device no longer exists.' },
  },
  {
    nom: 'le chargement du réglage de confidentialité',
    monter: () => <ConfidentialitePanel />,
    programmer: (e) => { vi.mocked(getCloudUltra).mockRejectedValue(e) },
    agir: async () => {},
    repli: { fr: 'Le réglage n’a pas pu être chargé.', en: 'The setting could not be loaded.' },
  },
  {
    nom: 'l’enregistrement du réglage de confidentialité',
    monter: () => <ConfidentialitePanel />,
    programmer: (e) => { vi.mocked(setCloudUltra).mockRejectedValue(e) },
    agir: async (user, c) => {
      await user.click(await screen.findByRole('button', { name: /Autoriser|Allow/ }))
      await user.click(c.querySelector('.danger-subtle') as HTMLElement)
    },
    repli: { fr: 'Le réglage n’a pas pu être enregistré.', en: 'The setting could not be saved.' },
  },
  {
    nom: 'le chargement des partages',
    monter: () => <PartageDialog type="projet" cible="p1" nom="Clients" onClose={() => {}} />,
    programmer: (e) => { vi.mocked(getPartages).mockRejectedValue(e) },
    agir: async () => {},
    repli: { fr: 'Les partages n’ont pas pu être chargés.', en: 'Shares could not be loaded.' },
  },
  {
    nom: 'la création d’un partage',
    monter: () => <PartageDialog type="projet" cible="p1" nom="Clients" onClose={() => {}} />,
    programmer: (e) => { vi.mocked(creerPartage).mockRejectedValue(e) },
    agir: async (user, c) => { await screen.findByText('bruno'); await user.type(screen.getByLabelText(/Identifiant du compte|Account username/), 'carole'); await user.click(c.ownerDocument.querySelector('button[type=submit]') as HTMLElement) },
    repli: { fr: 'Le partage n’a pas abouti.', en: 'The share did not complete.' },
    code: { code: 'share.self', fr: 'Un partage se fait avec un autre compte.', en: 'A share is made with another account.' },
  },
  {
    nom: 'le retrait d’un partage',
    monter: () => <PartageDialog type="projet" cible="p1" nom="Clients" onClose={() => {}} />,
    programmer: (e) => { vi.mocked(retirerPartage).mockRejectedValue(e) },
    agir: async (user, c) => { await screen.findByText('bruno'); await user.click(c.ownerDocument.querySelector('button.danger-subtle') as HTMLElement) },
    repli: { fr: 'Le partage n’a pas pu être retiré.', en: 'The share could not be removed.' },
    code: { code: 'share.unknown', fr: 'Ce partage est introuvable.', en: 'This share cannot be found.' },
  },
]

async function verifier(cas: Cas, erreur: unknown, lang: 'fr' | 'en', attendu: string) {
  const user = userEvent.setup()
  cas.programmer(erreur)
  const { container } = render(<LangProvider initiale={lang}>{cas.monter()}</LangProvider>)
  await cas.agir(user, container)
  const alerte = await screen.findByRole('alert')
  expect((alerte.textContent ?? '').trim()).toBe(attendu)
  expect(document.body.textContent).not.toMatch(/HTTP|base indisponible|phrase du serveur/)
}

for (const cas of CAS) {
  it(`${cas.nom} : une panne dit la phrase du contexte, dans la langue choisie, sans texte brut`, async () => {
    await verifier(cas, BRUT, 'fr', cas.repli.fr)
  })

  it(`${cas.nom} : la même panne en anglais`, async () => {
    await verifier(cas, BRUT, 'en', cas.repli.en)
  })

  if (cas.code) {
    const { code, fr, en } = cas.code
    it(`${cas.nom} : un refus à code connu donne la phrase du catalogue, jamais celle du serveur`, async () => {
      await verifier(cas, new ErreurAvecCode(code, 'phrase du serveur'), 'fr', fr)
    })

    it(`${cas.nom} : le même refus en anglais`, async () => {
      await verifier(cas, new ErreurAvecCode(code, 'phrase du serveur'), 'en', en)
    })
  }
}
