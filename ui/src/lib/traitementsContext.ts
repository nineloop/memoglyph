import { createContext, useContext } from 'react'
import type { ListeTraitements } from '../api'

/**
 * Les traitements du compte, offerts à tout écran qui en lance un (régénérer un compte
 * rendu depuis la réunion). `liste` est relevée par `useTraitements` ; `rafraichir()` la relève tout de suite après
 * un lancement ; `suivre()` ouvre la vue des traitements. Sans fournisseur, un écran isolé (test, composant seul)
 * garde un comportement neutre.
 */
export interface TraitementsCourants {
  liste: ListeTraitements | null
  rafraichir: () => void
  suivre: () => void
}

export const TraitementsContext = createContext<TraitementsCourants>({
  liste: null,
  rafraichir: () => {},
  suivre: () => {},
})

export function useTraitementsCourants(): TraitementsCourants {
  return useContext(TraitementsContext)
}
