import { useId, useRef } from 'react'
import type { KeyboardEvent } from 'react'

/**
 * Une barre d'onglets accessible au clavier : un seul onglet dans l'ordre de tabulation (celui qui est sélectionné), les flèches, Début et Fin
 * déplacent la sélection ET le focus (activation automatique, retour au bout opposé), chaque onglet a un identifiant et l'onglet
 * sélectionné dit quel panneau il commande (`aria-controls` : seul le panneau actif est sûr d'exister).
 * `ids` : les onglets PRÉSENTS, dans l'ordre (un onglet masqué par les droits n'y figure pas).
 */
export function useBarreOnglets<T extends string>(ids: readonly T[], courant: T, choisir: (id: T) => void) {
  const base = useId()
  const boutons = useRef(new Map<T, HTMLButtonElement | null>())
  const idOnglet = (id: T) => `${base}-onglet-${id}`
  const idPanneau = (id: T) => `${base}-panneau-${id}`

  const surTouche = (id: T) => (evenement: KeyboardEvent<HTMLButtonElement>) => {
    const rang = ids.indexOf(id)
    let cible: T | undefined
    if (evenement.key === 'ArrowRight') cible = ids[(rang + 1) % ids.length]
    else if (evenement.key === 'ArrowLeft') cible = ids[(rang - 1 + ids.length) % ids.length]
    else if (evenement.key === 'Home') cible = ids[0]
    else if (evenement.key === 'End') cible = ids[ids.length - 1]
    if (cible === undefined) return
    evenement.preventDefault()
    choisir(cible)
    boutons.current.get(cible)?.focus()   // le focus suit la sélection : sans lui, Tab partirait de l'ancien onglet (tabIndex -1)
  }

  const onglet = (id: T) => ({
    id: idOnglet(id),
    role: 'tab' as const,
    'aria-selected': courant === id,
    'aria-controls': courant === id ? idPanneau(id) : undefined,
    tabIndex: courant === id ? 0 : -1,
    onKeyDown: surTouche(id),
    ref: (element: HTMLButtonElement | null) => { boutons.current.set(id, element) },
  })

  return { onglet, idOnglet, idPanneau }
}
