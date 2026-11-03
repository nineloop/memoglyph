import { useCallback, useEffect, useRef, useState } from 'react'
import type { ListeTraitements } from '../api'
import { getTraitements } from '../api'
import { demarrerReleve } from './traitements'

/**
 * La liste des traitements du compte, relevée à son rythme (voir `demarrerReleve`).
 * `rafraichir()` relève tout de suite : après un envoi, le traitement apparaît sans attendre.
 */
export function useTraitements(): { liste: ListeTraitements | null; rafraichir: () => void } {
  const [liste, setListe] = useState<ListeTraitements | null>(null)
  const releve = useRef<ReturnType<typeof demarrerReleve> | null>(null)

  useEffect(() => {
    const r = demarrerReleve({ recuperer: getTraitements, surListe: setListe })
    releve.current = r
    return () => { r.arreter(); releve.current = null }
  }, [])

  const rafraichir = useCallback(() => { releve.current?.rafraichir() }, [])
  return { liste, rafraichir }
}
