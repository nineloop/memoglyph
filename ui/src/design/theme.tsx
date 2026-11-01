import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ecrireCle, lireCle } from '../lib/stockage'
import './theme.css'

/**
 * Thème de l'interface — clair / sombre / système.
 *
 * Les JETONS de couleur vivent déjà dans `index.css` (charte de l'interface,
 * auditée WCAG) et `design/theme.css` (variante sombre explicite). Ce module ne
 * fait que TRANCHER le thème effectif et l'inscrire en `data-theme` sur la
 * racine — il ne redéfinit aucune couleur.
 *
 * `matchMedia` est sondé, jamais supposé : jsdom ne l'expose pas, et un accès nu
 * ferait planter chaque test qui monte l'application.
 */
export type ThemeChoice = 'system' | 'light' | 'dark'
type Resolved = 'light' | 'dark'

const CLE = 'theme'

type ThemeCtx = { choice: ThemeChoice; resolved: Resolved; setChoice: (c: ThemeChoice) => void }
const Ctx = createContext<ThemeCtx | null>(null)

function prefereSombre(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-color-scheme: dark)').matches
}

function lireChoix(): ThemeChoice {
  try {
    const v = lireCle(CLE)
    if (v === 'light' || v === 'dark' || v === 'system') return v
  } catch { /* stockage indisponible : on retombe sur « système » */ }
  return 'system'
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(() => lireChoix())
  const [systemeSombre, setSystemeSombre] = useState<boolean>(() => prefereSombre())

  // Le mode système suit l'OS EN DIRECT : bascule automatique sans rechargement.
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const surChangement = () => setSystemeSombre(mq.matches)
    mq.addEventListener?.('change', surChangement)
    return () => mq.removeEventListener?.('change', surChangement)
  }, [])

  const resolved: Resolved = choice === 'system' ? (systemeSombre ? 'dark' : 'light') : choice

  // On inscrit TOUJOURS un thème explicite : la requête média de `index.css` ne
  // sert plus que d'anti-flash avant que React ne s'exécute.
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', resolved)
  }, [resolved])

  const setChoice = useCallback((c: ThemeChoice) => {
    setChoiceState(c)
    try { ecrireCle(CLE, c) } catch { /* pas de persistance : sans effet fonctionnel */ }
  }, [])

  const value = useMemo(() => ({ choice, resolved, setChoice }), [choice, resolved, setChoice])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useTheme(): ThemeCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useTheme doit être utilisé dans un ThemeProvider')
  return ctx
}
