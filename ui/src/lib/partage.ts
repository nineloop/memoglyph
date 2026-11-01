import { createContext, useContext } from 'react'

/**
 * Ce que la portée courante permet (partage v1).
 *
 * `lectureSeule` : la portée est « Partagés avec moi », ou un projet reçu. Toute
 * action d'écriture y est masquée ; le serveur la refuserait de toute façon
 * (404). `peutPartager` : le compte est propriétaire de ce projet, donc peut le
 * partager, lui ou ses réunions.
 */
export interface PartageCourant {
  lectureSeule: boolean
  peutPartager: (projectId: string | null | undefined) => boolean
}

export const PartageContext = createContext<PartageCourant>({
  lectureSeule: false,
  peutPartager: () => false,
})

export function usePartage(): PartageCourant {
  return useContext(PartageContext)
}
