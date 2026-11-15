import { useCallback, useEffect, useRef, useState } from 'react'
import type { ListeTraitements } from '../api'
import { getTraitements } from '../api'
import { demarrerReleve } from './traitements'

/** Où en est la relève : `chargement` tant qu'aucune liste n'est arrivée, `ok` ensuite, `echec` quand un tour échoue. */
export type EtatReleve = 'chargement' | 'ok' | 'echec'

/**
 * La liste des traitements du compte, relevée à son rythme (voir `demarrerReleve`), et l'état de cette relève.
 * `rafraichir()` relève tout de suite : après un envoi, le traitement apparaît sans attendre ; tant qu'aucune liste
 * n'est arrivée, il repasse l'état à `chargement`. La dernière liste reste disponible après un échec.
 */
export function useTraitements(): { liste: ListeTraitements | null; etat: EtatReleve; rafraichir: () => void } {
  const [liste, setListe] = useState<ListeTraitements | null>(null)
  const [etat, setEtat] = useState<EtatReleve>('chargement')
  const releve = useRef<ReturnType<typeof demarrerReleve> | null>(null)
  const aUneListe = useRef(false)

  useEffect(() => {
    const r = demarrerReleve({
      recuperer: getTraitements,
      surListe: (l) => { aUneListe.current = true; setListe(l); setEtat('ok') },
      surEchec: () => setEtat('echec'),
    })
    releve.current = r
    return () => { r.arreter(); releve.current = null }
  }, [])

  const rafraichir = useCallback(() => {
    if (!aUneListe.current) setEtat('chargement')
    releve.current?.rafraichir()
  }, [])
  return { liste, etat, rafraichir }
}
