import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { LangProvider } from '../components/LangProvider'
import { SessionContext } from '../lib/sessionContext'
import { MenuReglages } from '../components/MenuReglages'
import { ThemeProvider } from '../design/theme'

function monter(admin = false, deconnexion = vi.fn(async () => {})) {
  render(
    <LangProvider><ThemeProvider>
      <SessionContext.Provider value={{ utilisateur: { identifiant: 'alice', admin }, deconnexion }}>
        <MenuReglages />
      </SessionContext.Provider>
    </ThemeProvider></LangProvider>)
  return { deconnexion }
}

beforeEach(() => { localStorage.clear(); localStorage.setItem('memoglyph.lang', 'fr') })

describe('le menu « Réglages »', () => {
  it('porte l\'initiale du compte et reste fermé au départ', () => {
    monter()
    const bouton = screen.getByRole('button', { name: /réglages/i })
    expect(bouton).toHaveAttribute('aria-expanded', 'false')
    expect(bouton).toHaveTextContent('A')
    expect(screen.queryByRole('button', { name: /^appareils$/i })).not.toBeInTheDocument()
  })

  it('regroupe Appareils, Confidentialité, langue, thème et Se déconnecter', async () => {
    monter()
    await userEvent.click(screen.getByRole('button', { name: /réglages/i }))
    const menu = screen.getByRole('group', { name: /réglages/i })
    for (const nom of [/^appareils$/i, /^confidentialité$/i, /se déconnecter/i]) {
      expect(within(menu).getByRole('button', { name: nom })).toBeInTheDocument()
    }
    expect(within(menu).getByRole('combobox', { name: /langue/i })).toBeInTheDocument()
    expect(within(menu).getByRole('group', { name: /thème|theme/i })).toBeInTheDocument()
  })

  it('« Comptes » n\'apparaît que pour un administrateur', async () => {
    monter(false)
    await userEvent.click(screen.getByRole('button', { name: /réglages/i }))
    expect(screen.queryByRole('button', { name: /^comptes$/i })).not.toBeInTheDocument()
  })

  it('« Comptes » apparaît pour un administrateur', async () => {
    monter(true)
    await userEvent.click(screen.getByRole('button', { name: /réglages/i }))
    expect(screen.getByRole('button', { name: /^comptes$/i })).toBeInTheDocument()
  })

  it('Échap ferme le menu et rend le focus au bouton', async () => {
    monter()
    const bouton = screen.getByRole('button', { name: /réglages/i })
    await userEvent.click(bouton)
    await userEvent.keyboard('{Escape}')
    expect(bouton).toHaveAttribute('aria-expanded', 'false')
    expect(bouton).toHaveFocus()
  })

  it('la flèche bas ouvre le menu et place le focus sur le premier réglage ; les flèches circulent', async () => {
    monter()
    const bouton = screen.getByRole('button', { name: /réglages/i })
    bouton.focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(bouton).toHaveAttribute('aria-expanded', 'true')
    const premier = document.activeElement as HTMLElement
    expect(premier).not.toBe(bouton)
    await userEvent.keyboard('{ArrowDown}')
    expect(document.activeElement).not.toBe(premier)
    await userEvent.keyboard('{ArrowUp}')
    expect(document.activeElement).toBe(premier)
  })

  it('un clic hors du menu le ferme', async () => {
    monter()
    await userEvent.click(screen.getByRole('button', { name: /réglages/i }))
    await userEvent.click(document.body)
    expect(screen.getByRole('button', { name: /réglages/i })).toHaveAttribute('aria-expanded', 'false')
  })

  it('changer la langue ne ferme pas le menu et change le texte sans quitter l\'écran', async () => {
    monter()
    await userEvent.click(screen.getByRole('button', { name: /réglages/i }))
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /langue/i }), 'en')
    expect(screen.getByRole('button', { name: /settings/i })).toHaveAttribute('aria-expanded', 'true')
  })

  it('Se déconnecter appelle la déconnexion', async () => {
    const { deconnexion } = monter()
    await userEvent.click(screen.getByRole('button', { name: /réglages/i }))
    await userEvent.click(screen.getByRole('button', { name: /se déconnecter/i }))
    expect(deconnexion).toHaveBeenCalled()
  })

  it('Appareils ouvre sa fenêtre malgré la fermeture du menu', async () => {
    monter()
    await userEvent.click(screen.getByRole('button', { name: /réglages/i }))
    await userEvent.click(screen.getByRole('button', { name: /^appareils$/i }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('Échap dans la fenêtre Appareils ferme la fenêtre, pas le menu qui l\'a ouverte', async () => {
    monter()
    await userEvent.click(screen.getByRole('button', { name: /réglages/i }))
    screen.getByRole('button', { name: /^appareils$/i }).focus()
    await userEvent.keyboard('{Enter}')
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('les flèches dans la fenêtre ne déplacent pas le focus dans le menu', async () => {
    monter()
    await userEvent.click(screen.getByRole('button', { name: /réglages/i }))
    await userEvent.click(screen.getByRole('button', { name: /^appareils$/i }))
    const dialogue = await screen.findByRole('dialog')
    const fermer = within(dialogue).getAllByRole('button')[0]
    fermer.focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(dialogue.contains(document.activeElement)).toBe(true)
  })
})
