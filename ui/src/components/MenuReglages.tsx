import { useEffect, useId, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { useLang } from '../lib/langContext'
import { ThemeToggle } from '../design/primitives'
import { BoutonAppareils } from './AppareilsPanel'
import { BoutonDeconnexion, useSession } from './AuthGate'
import { BoutonComptes } from './ComptesPanel'
import { BoutonConfidentialite } from './ConfidentialitePanel'
import { LangSelector } from './LangProvider'

/**
 * Le menu « Réglages » de la barre haute : tout ce qui n'est pas de la
 * navigation y est regroupé (Appareils, Confidentialité, Comptes, langue, thème, Se déconnecter).
 *
 * Patron « divulgation » : un bouton `aria-expanded` et un groupe, plutôt qu'un `role="menu"`
 * qui promettrait des éléments de menu alors que le groupe contient une liste de langues et un
 * choix de thème. Le groupe reste MONTÉ quand il est fermé (`hidden`) : les fenêtres d'Appareils,
 * de Confidentialité et de Comptes appartiennent à leur bouton, les démonter fermerait la
 * fenêtre qu'on vient d'ouvrir.
 */
const FOCALISABLES = 'button:not([disabled]), select:not([disabled]), [role="radio"]:not([disabled]), a[href]'

export function MenuReglages() {
  const { t } = useLang()
  const session = useSession()
  const [ouvert, setOuvert] = useState(false)
  const racine = useRef<HTMLDivElement>(null)
  const bouton = useRef<HTMLButtonElement>(null)
  const groupe = useRef<HTMLDivElement>(null)
  const idGroupe = useId()
  const compte = session?.utilisateur?.identifiant ?? ''
  const initiale = compte ? compte.charAt(0).toLocaleUpperCase() : ''

  function elements(): HTMLElement[] {
    return Array.from(groupe.current?.querySelectorAll<HTMLElement>(FOCALISABLES) ?? [])
  }

  function fermer(rendreFocus: boolean) {
    setOuvert(false)
    if (rendreFocus) bouton.current?.focus()
  }

  useEffect(() => {
    if (!ouvert) return
    const dehors = (e: MouseEvent) => {
      if (racine.current && !racine.current.contains(e.target as Node)) setOuvert(false)
    }
    document.addEventListener('mousedown', dehors)
    return () => document.removeEventListener('mousedown', dehors)
  }, [ouvert])

  function sur(e: KeyboardEvent<HTMLDivElement>) {
    // Les fenêtres d'Appareils, de Confidentialité et de Comptes sont des portails : leurs
    // événements remontent ici dans l'arbre React sans être DANS le menu, et leur bouton garde le
    // focus quand elles s'ouvrent. Tant qu'une fenêtre est ouverte, son Échap et ses flèches
    // lui appartiennent : on ne détourne rien.
    if (document.querySelector('[role="dialog"][aria-modal="true"]')) return
    if (e.key === 'Escape' && ouvert) { e.stopPropagation(); fermer(true); return }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    // Dans une liste de langues native, les flèches changent la valeur : on les laisse faire.
    if ((e.target as HTMLElement).tagName === 'SELECT') return
    e.preventDefault()
    const tous = elements()
    if (!ouvert) {
      setOuvert(true)
      setTimeout(() => elements()[0]?.focus(), 0)
      return
    }
    const i = tous.indexOf(document.activeElement as HTMLElement)
    const suivant = e.key === 'ArrowDown' ? (i + 1) % tous.length : (i <= 0 ? tous.length - 1 : i - 1)
    tous[suivant]?.focus()
  }

  return (
    <div className="reglages" ref={racine} onKeyDown={sur}>
      <button type="button" ref={bouton} className="chip reglages-bouton"
              aria-expanded={ouvert} aria-controls={idGroupe} aria-label={t('reglages.button')}
              title={compte ? t('reglages.account', { nom: compte }) : t('reglages.button')}
              onClick={() => setOuvert((o) => !o)}>
        {initiale && <span className="reglages-initiale" aria-hidden="true">{initiale}</span>}
        <span className="reglages-texte">{t('reglages.button')}</span>
      </button>
      <div id={idGroupe} ref={groupe} role="group" aria-label={t('reglages.menu.aria')}
           className="reglages-menu" hidden={!ouvert}>
        <BoutonAppareils />
        <BoutonConfidentialite />
        <BoutonComptes />
        <div className="reglages-ligne"><span>{t('reglages.language')}</span><LangSelector /></div>
        <div className="reglages-ligne"><span>{t('reglages.theme')}</span><ThemeToggle /></div>
        <BoutonDeconnexion />
      </div>
    </div>
  )
}
