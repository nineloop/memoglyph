/**
 * Les actions ont la couleur d'action (le teal) ; le violet marque seulement le contenu produit par un modèle
 * (voir le commentaire de tête d'index.css). Un bouton « Demander », « Régénérer » ou « Compte rendu » n'est pas du contenu généré.
 */
import { describe, expect, it } from 'vitest'
import appCss from '../App.css?raw'
import ask from '../components/AskPanel.tsx?raw'
import regenerer from '../components/RegenererCompteRendu.tsx?raw'
import reader from '../components/TranscriptReader.tsx?raw'
import meeting from '../components/MeetingView.tsx?raw'

describe('boutons d’action : pas de violet', () => {
  it.each([['AskPanel', ask], ['RegenererCompteRendu', regenerer], ['TranscriptReader', reader], ['MeetingView', meeting]])(
    '%s : aucun bouton « primary llm » ni « btn accent llm »', (_nom, source) => {
      expect(source).not.toMatch(/className="(?:primary|btn accent) llm"/)
    })

  it('les règles de bouton violet ne subsistent pas dans la feuille de style', () => {
    expect(appCss).not.toMatch(/\.primary\.llm\b/)
    expect(appCss).not.toMatch(/\.btn\.accent\.llm\b/)
  })
})
