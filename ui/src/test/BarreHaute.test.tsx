import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SessionContext } from '../lib/sessionContext'
import { BarreHaute } from '../components/BarreHaute'
import { LangProvider } from '../components/LangProvider'
import { ThemeProvider } from '../design/theme'

function monter(props: Partial<React.ComponentProps<typeof BarreHaute>> = {}) {
  const surAccueil = vi.fn()
  const surRecherche = vi.fn()
  render(
    <LangProvider><ThemeProvider>
      <SessionContext.Provider value={{ utilisateur: { identifiant: 'alice', admin: true }, deconnexion: async () => {} }}>
        <BarreHaute llm={true} diarisation={false} llmEtat="LLM prêt" recherche="" surRecherche={surRecherche} surChangerRecherche={() => {}}
                    surAccueil={surAccueil} {...props} />
      </SessionContext.Provider>
    </ThemeProvider></LangProvider>)
  return { surAccueil, surRecherche }
}

beforeEach(() => { localStorage.clear(); localStorage.setItem('memoglyph.lang', 'fr') })

describe('la barre haute allégée (US3)', () => {
  it('contient le logo, la recherche, l\'état des services, l\'indicateur et Réglages, rien d\'autre', () => {
    monter({ indicateur: <button type="button">Traitements</button> })
    const barre = screen.getByRole('banner')
    expect(within(barre).getByRole('link', { name: /memoglyph/i })).toBeInTheDocument()
    expect(within(barre).getByRole('search')).toBeInTheDocument()
    expect(within(barre).getByLabelText(/état des services|services status/i)).toBeInTheDocument()
    expect(within(barre).getByRole('button', { name: 'Traitements' })).toBeInTheDocument()
    expect(within(barre).getByRole('button', { name: /réglages/i })).toBeInTheDocument()
    // Rien d'autre, visible d'emblée : ni liste de portée, ni langue, ni thème, ni déconnexion (dans Réglages).
    expect(within(barre).queryByRole('combobox', { name: /portée|scope/i })).not.toBeInTheDocument()
    expect(within(barre).queryByRole('button', { name: /partager|share/i })).not.toBeInTheDocument()
  })

  it('le logo est un lien vers Transcrire (l\'accueil) ; le clic simple navigue sans recharger', async () => {
    const { surAccueil } = monter()
    const logo = screen.getByRole('link', { name: /memoglyph/i })
    expect(logo).toHaveAttribute('href', expect.stringContaining('vue=transcribe'))
    await userEvent.click(logo)
    expect(surAccueil).toHaveBeenCalledTimes(1)
  })

  it('la recherche rapide envoie la saisie à surRecherche, vide ou non', async () => {
    const { surRecherche } = monter({ recherche: 'budget' })
    await userEvent.click(screen.getByRole('button', { name: /lancer la recherche|run search/i }))
    expect(surRecherche).toHaveBeenCalledWith('budget')
  })

  it('la recherche est retirée sur l\'écran Recherche, où elle ferait doublon', () => {
    monter({ masquerRecherche: true })
    expect(screen.queryByRole('search')).not.toBeInTheDocument()
  })

  it('l\'état des services donne un nom lisible, pas seulement une couleur', () => {
    monter()
    const etat = screen.getByLabelText(/état des services|services status/i)
    expect(etat).toHaveTextContent(/llm/i)
    expect(etat.querySelectorAll('.dot.ok').length).toBe(1)
    expect(etat.querySelectorAll('.dot.off').length).toBe(1)
  })
})
