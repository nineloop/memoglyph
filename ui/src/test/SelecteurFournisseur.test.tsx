/**
 * Le sélecteur de fournisseur partagé par Transcrire et Régénérer : « Local » d'abord, « En ligne » repliable avec chaque
 * fournisseur et son état, les non configurés en grisé avec une aide d'une ligne (sans nom de variable), la règle de sensibilité
 * reflétée (le serveur garde le dernier mot).
 */
import { useState } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { AppConfig } from '../api'
import { LangProvider } from '../components/LangProvider'
import { SelecteurFournisseur } from '../components/SelecteurFournisseur'

const CONFIG = {
  llm_etats: {
    mlx: { lieu: 'local', etat: 'pret' },
    groq: { lieu: 'en_ligne', etat: 'pret' },
    openrouter: { lieu: 'en_ligne', etat: 'gratuit' },
    mistral: { lieu: 'en_ligne', etat: 'non_configure' },
    compatible: { lieu: 'cloud_prive', etat: 'pret' },
  },
  llm_models: { mlx: 'Qwen3-8B', groq: 'un-modele-groq', openrouter: 'auteur/un-modele:free', compatible: 'qwen-prive' },
} as unknown as AppConfig

function monter(options: { valeur?: string; ultra?: boolean; cloudUltra?: boolean; desactive?: boolean; enLigneOuvert?: boolean; langue?: 'fr' | 'en';
  config?: AppConfig; surChoix?: (p: string) => void } = {}) {
  const surChoix = options.surChoix ?? vi.fn()
  function Hote() {
    const [valeur, setValeur] = useState(options.valeur ?? 'mlx')
    return (
      <LangProvider initiale={options.langue ?? 'fr'}>
        <SelecteurFournisseur config={options.config ?? CONFIG} valeur={valeur} nom="essai" legende="Où traiter"
          ultra={options.ultra} cloudUltra={options.cloudUltra} desactive={options.desactive} enLigneOuvert={options.enLigneOuvert}
          onChange={(p) => { setValeur(p); surChoix(p) }} />
      </LangProvider>
    )
  }
  return { ...render(<Hote />), surChoix }
}

const ouvrirEnLigne = () => userEvent.click(screen.getByRole('button', { name: /En ligne|Online/ }))

describe('le sélecteur', () => {
  it.each([['fr', 'Local', 'En ligne'], ['en', 'Local', 'Online']] as const)(
    'les deux lieux sont nommés (%s) : l\'intitulé « Local » au-dessus du modèle local, « En ligne » au-dessus des autres', (langue, local, enLigne) => {
      monter({ langue })
      const groupe = screen.getByRole('group', { name: 'Où traiter' })
      expect(within(groupe).getByText(local)).toBeInTheDocument()
      expect(within(groupe).getByRole('button', { name: enLigne })).toBeInTheDocument()
      // « Local » nomme la liste du modèle local ; ce n'est pas un bouton, il ne se replie pas et ne prend pas le focus.
      expect(within(groupe).getByRole('list', { name: local })).toContainElement(within(groupe).getByRole('radio', { name: /MLX/ }))
      expect(within(groupe).queryByRole('button', { name: local })).toBeNull()
      // L'intitulé précède la liste, et « En ligne » vient après.
      const intitule = within(groupe).getByText(local)
      expect(intitule.compareDocumentPosition(within(groupe).getByRole('radio', { name: /MLX/ })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      expect(intitule.compareDocumentPosition(within(groupe).getByRole('button', { name: enLigne })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })

  it('sans fournisseur local, pas d\'intitulé « Local » vide', () => {
    monter({ config: { llm_etats: { groq: { lieu: 'en_ligne', etat: 'pret' } }, llm_models: {} } as unknown as AppConfig })
    expect(screen.queryByText('Local')).not.toBeInTheDocument()
  })

  it('le bouton « En ligne » montre un chevron (bas replié, haut déplié), jamais une croix ni un plus', async () => {
    monter()
    const forme = () => screen.getByRole('button', { name: 'En ligne' }).querySelector('path')?.getAttribute('d')
    const CHEVRON_BAS = 'm5 8 6 6 6-6'
    const CHEVRON_HAUT = 'm5 14 6-6 6 6'
    expect(forme()).toBe(CHEVRON_BAS)
    await userEvent.click(screen.getByRole('button', { name: 'En ligne' }))
    expect(forme()).toBe(CHEVRON_HAUT)
  })

  it('le fournisseur en ligne choisi du dehors (réglages du compte arrivés après l\'affichage) se dévoile : « En ligne » s\'ouvre', () => {
    const rendu = (valeur: string) => (
      <LangProvider initiale="fr">
        <SelecteurFournisseur config={CONFIG} valeur={valeur} nom="essai" legende="Où traiter" onChange={() => {}} />
      </LangProvider>)
    const { rerender } = render(rendu('mlx'))
    expect(screen.queryByRole('radio', { name: /Groq/ })).not.toBeInTheDocument()
    rerender(rendu('groq'))
    expect(screen.getByRole('radio', { name: /Groq/ })).toBeChecked()
    expect(screen.getByRole('button', { name: 'En ligne' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('montre « Local » d\'abord et « En ligne » replié : les fournisseurs en ligne n\'y sont pas avant l\'ouverture', async () => {
    monter()
    const groupe = screen.getByRole('group', { name: 'Où traiter' })
    expect(within(groupe).getByRole('radio', { name: /MLX/ })).toBeChecked()
    const bouton = screen.getByRole('button', { name: 'En ligne' })
    expect(bouton).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('radio', { name: /Groq/ })).not.toBeInTheDocument()

    await userEvent.click(bouton)

    expect(bouton).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('radio', { name: /Groq/ })).toBeEnabled()
    expect(bouton).toHaveAttribute('aria-controls')
  })

  it('s\'ouvre dès l\'affichage quand l\'écran le demande (« Régénérer »)', () => {
    monter({ enLigneOuvert: true })
    expect(screen.getByRole('button', { name: 'En ligne' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('radio', { name: /Groq/ })).toBeEnabled()
  })

  it('s\'ouvre tout seul quand le fournisseur choisi est en ligne', () => {
    monter({ valeur: 'groq' })
    expect(screen.getByRole('button', { name: 'En ligne' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('radio', { name: /Groq/ })).toBeChecked()
  })

  it('un fournisseur non configuré est grisé, non choisissable, avec une aide d\'une ligne SANS nom de variable', async () => {
    monter()
    await ouvrirEnLigne()
    expect(screen.queryByRole('radio', { name: /Mistral/ })).not.toBeInTheDocument()
    const ligne = screen.getByText('Mistral').closest('li') as HTMLElement
    expect(ligne).toHaveAttribute('aria-disabled', 'true')
    expect(ligne).toHaveTextContent('Non configuré')
    expect(ligne).toHaveTextContent('Ajouter ce fournisseur : voir la page Local et en ligne de la documentation.')
    expect(ligne.textContent).not.toMatch(/[A-Z]{3,}_[A-Z_]{3,}|\.env|API_KEY/)
  })

  it('aucun fournisseur en ligne prêt : les lignes grisées ne répètent pas la même aide (l\'écran la dit une fois)', async () => {
    const config = { ...CONFIG, llm_etats: {
      mlx: { lieu: 'local', etat: 'pret' }, groq: { lieu: 'en_ligne', etat: 'non_configure' },
      mistral: { lieu: 'en_ligne', etat: 'non_configure' }, compatible: { lieu: 'cloud_prive', etat: 'non_configure' },
    } } as unknown as AppConfig
    monter({ config, enLigneOuvert: true })
    for (const nom of ['Groq', 'Mistral', 'Serveur compatible OpenAI']) {
      const ligne = screen.getByText(nom).closest('li') as HTMLElement
      expect(ligne).toHaveAttribute('aria-disabled', 'true')
      expect(ligne).toHaveTextContent('Non configuré')
    }
    expect(screen.queryByText(/Ajouter ce fournisseur/)).not.toBeInTheDocument()
  })

  it('dit « modèle gratuit » et reste choisissable ; le cloud privé est rangé avec « En ligne »', async () => {
    monter()
    await ouvrirEnLigne()
    const gratuit = screen.getByRole('radio', { name: /OpenRouter/ })
    expect(gratuit).toBeEnabled()
    expect((gratuit.closest('label') as HTMLElement)).toHaveTextContent('Modèle gratuit')
    const prive = screen.getByRole('radio', { name: /Serveur compatible/ })
    expect((prive.closest('label') as HTMLElement)).toHaveTextContent('Cloud privé')
  })

  it('montre le modèle configuré de chaque fournisseur prêt, jamais une clé', async () => {
    monter()
    await ouvrirEnLigne()
    expect(screen.getByText('Qwen3-8B')).toBeInTheDocument()
    expect(screen.getByText('un-modele-groq')).toBeInTheDocument()
  })

  it('ultra-confidentiel sans autorisation : tout « En ligne » est désactivé, avec « Local uniquement » ; le local reste choisissable', async () => {
    monter({ ultra: true })
    expect(screen.getByRole('button', { name: /En ligne/ })).toHaveTextContent('Local uniquement')
    await ouvrirEnLigne()
    for (const nom of [/Groq/, /OpenRouter/, /Serveur compatible/]) expect(screen.getByRole('radio', { name: nom })).toBeDisabled()
    // La raison est un TEXTE visible lié à l'option (une option désactivée ne reçoit pas le focus : pas d'infobulle seule).
    const raisons: [RegExp, string][] = [
      [/Groq/, 'Non autorisé pour la sensibilité de ce document'],
      [/Serveur compatible/, 'Non autorisé pour la sensibilité de ce document'],
      [/OpenRouter/, 'Un modèle gratuit ne reçoit pas de document ultra-confidentiel.'],      // un modèle gratuit : sa propre raison
    ]
    for (const [nom, texte] of raisons) {
      const raison = screen.getByRole('radio', { name: nom }).getAttribute('aria-describedby')
      expect(raison).toBeTruthy()
      expect(document.getElementById(raison!)).toHaveTextContent(texte)
    }
    expect(screen.getByRole('radio', { name: /MLX/ }).getAttribute('aria-describedby')).toBeNull()
    expect(screen.getByRole('radio', { name: /MLX/ })).toBeEnabled()
  })

  it('ultra-confidentiel autorisé par le propriétaire : « En ligne » se choisit, SAUF un modèle gratuit (refusé quoi qu\'il arrive)', async () => {
    monter({ ultra: true, cloudUltra: true })
    await ouvrirEnLigne()
    expect(screen.getByRole('radio', { name: /Groq/ })).toBeEnabled()
    const gratuit = screen.getByRole('radio', { name: /OpenRouter/ })
    expect(gratuit).toBeDisabled()
    expect((gratuit.closest('label') as HTMLElement)).toHaveTextContent('Un modèle gratuit ne reçoit pas de document ultra-confidentiel.')
  })

  it('hors ultra, un modèle gratuit se choisit', async () => {
    monter({ ultra: false })
    await ouvrirEnLigne()
    expect(screen.getByRole('radio', { name: /OpenRouter/ })).toBeEnabled()
  })

  it('rend le choix à l\'appelant et le coche', async () => {
    const { surChoix } = monter()
    await ouvrirEnLigne()
    await userEvent.click(screen.getByRole('radio', { name: /Groq/ }))
    expect(surChoix).toHaveBeenCalledWith('groq')
    expect(screen.getByRole('radio', { name: /Groq/ })).toBeChecked()
  })

  it('désactivé : plus rien ne se choisit (un traitement tourne)', async () => {
    monter({ desactive: true })
    await ouvrirEnLigne()
    for (const radio of screen.getAllByRole('radio')) expect(radio).toBeDisabled()
  })

  it('une aide dit quand le modèle local lui-même n\'est pas prêt, sans parler de clé', () => {
    const config = { ...CONFIG, llm_etats: { ...CONFIG.llm_etats, mlx: { lieu: 'local', etat: 'non_configure' } } } as unknown as AppConfig
    monter({ config, valeur: 'groq' })
    const ligne = screen.getByText('MLX').closest('li') as HTMLElement
    expect(ligne).toHaveTextContent('Non disponible')
    expect(ligne.textContent).not.toMatch(/clé|key/i)
  })

  it('tout existe en anglais', async () => {
    monter({ langue: 'en', ultra: true })
    expect(screen.getByRole('group', { name: 'Où traiter' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Online/ })).toHaveTextContent('Local only')
    await userEvent.click(screen.getByRole('button', { name: /Online/ }))
    expect(screen.getByText('Mistral').closest('li')).toHaveTextContent('Not configured')
    expect(screen.getByText('Mistral').closest('li')).toHaveTextContent('To add this provider, see the Local and online page of the documentation.')
  })
})
