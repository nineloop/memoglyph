import { createContext } from 'react'
import type { Utilisateur } from '../api'

/** La session courante, partagée par la barrière de connexion et les écrans qui en dépendent. */
export interface Session {
  utilisateur: Utilisateur | null
  deconnexion: () => Promise<void>
}

export const SessionContext = createContext<Session | null>(null)
