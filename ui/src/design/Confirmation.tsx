import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { useLang } from '../lib/langContext'

/**
 * La confirmation dans la fenêtre de l'application, à la place de `window.confirm`.
 *
 * `const ok = await confirmer({ titre, phrase, details?, libelleAction })` rend `true` (confirmé) ou `false` (refusé, Échap, ou
 * demande faite alors qu'une fenêtre modale est déjà ouverte : une seule fenêtre à la fois, jamais d'empilement). Le focus entre sur
 * « Annuler », l'action la moins risquée ; Tab reste dans la fenêtre ; à la fermeture le focus revient à l'élément qui avait
 * déclenché la demande. Le bouton de confirmation porte le nom de l'action. Aucune confirmation ne demande de saisie.
 */
export interface DemandeConfirmation {
  titre: string
  phrase: string
  /** Lignes affichées en liste sous la phrase (par exemple les doublons à archiver). */
  details?: string[]
  /** Le nom de l'action sur le bouton de confirmation : « Vider la corbeille », jamais « OK ». */
  libelleAction: string
  /** Le nom du bouton qui ferme sans agir, quand « Annuler » prêterait à confusion (« Annuler le traitement » : « Garder le traitement »). Par défaut : « Annuler ». */
  libelleRefus?: string
}

type Confirmer = (demande: DemandeConfirmation) => Promise<boolean>
/** Ce que le fournisseur reçoit : la demande, et le mot « Annuler » dans la langue de l'appelant. */
type DemandeInterne = DemandeConfirmation & { libelleRefus: string }
type ConfirmerInterne = (demande: DemandeInterne) => Promise<boolean>

const Contexte = createContext<ConfirmerInterne | null>(null)

const SANS_FOURNISSEUR: ConfirmerInterne = () => Promise.reject(new Error('useConfirmer : aucun ConfirmationProvider au-dessus de ce composant'))

/** La fonction `confirmer` du fournisseur de la racine. Sans fournisseur, l'appel échoue bruyamment (jamais un « oui » ou un « non » muet). */
export function useConfirmer(): Confirmer {
  // Le « Annuler » de la fenêtre se dit dans la langue de l'appelant : le fournisseur n'a pas besoin de connaître la langue.
  const { t } = useLang()
  const confirmer = useContext(Contexte) ?? SANS_FOURNISSEUR
  const refus = t('common.cancel')
  return useCallback((demande) => confirmer({ ...demande, libelleRefus: demande.libelleRefus ?? refus }), [confirmer, refus])
}

export function ConfirmationProvider({ children }: { children: ReactNode }) {
  const [demande, setDemande] = useState<DemandeInterne | null>(null)
  const resolveur = useRef<((reponse: boolean) => void) | null>(null)
  const declencheur = useRef<HTMLElement | null>(null)

  const confirmer = useCallback<ConfirmerInterne>((nouvelle) => new Promise<boolean>((resoudre) => {
    // Une seule fenêtre à la fois, et jamais par-dessus un autre dialogue modal.
    if (resolveur.current || document.querySelector('[role="dialog"][aria-modal="true"]')) { resoudre(false); return }
    resolveur.current = resoudre
    declencheur.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setDemande(nouvelle)
  }), [])

  const fermer = useCallback((reponse: boolean) => {
    const resoudre = resolveur.current
    resolveur.current = null
    declencheur.current?.focus()
    declencheur.current = null
    setDemande(null)
    resoudre?.(reponse)
  }, [])

  return (
    <Contexte.Provider value={confirmer}>
      {children}
      {demande && <FenetreDeConfirmation demande={demande} onReponse={fermer} />}
    </Contexte.Provider>
  )
}

function FenetreDeConfirmation({ demande, onReponse }: { demande: DemandeInterne; onReponse: (reponse: boolean) => void }) {
  const idTitre = useId()
  const idPhrase = useId()
  const fenetre = useRef<HTMLDivElement>(null)
  const annuler = useRef<HTMLButtonElement>(null)

  useEffect(() => { annuler.current?.focus() }, [])

  // Échap ferme où que soit le focus, comme les autres dialogues : un écouteur sur le fond ne le recevrait que de l'intérieur.
  useEffect(() => {
    const echap = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') onReponse(false) }
    document.addEventListener('keydown', echap)
    return () => document.removeEventListener('keydown', echap)
  }, [onReponse])

  function tab(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Tab') return
    const boutons = Array.from(fenetre.current?.querySelectorAll<HTMLButtonElement>('button') ?? [])
    if (boutons.length === 0) return
    const premier = boutons[0]
    const dernier = boutons[boutons.length - 1]
    const actif = document.activeElement
    if (event.shiftKey && actif === premier) { event.preventDefault(); dernier.focus() }
    else if (!event.shiftKey && actif === dernier) { event.preventDefault(); premier.focus() }
  }

  return createPortal(
    <div className="modal-backdrop">
      <div ref={fenetre} className="merge-dialog confirmation-dialog" role="dialog" aria-modal="true"
           aria-labelledby={idTitre} aria-describedby={idPhrase} onKeyDown={tab}>
        <div className="merge-dialog-head"><h2 id={idTitre}>{demande.titre}</h2></div>
        <p id={idPhrase} className="confirmation-phrase">{demande.phrase}</p>
        {demande.details && demande.details.length > 0 && (
          <ul className="confirmation-details">
            {demande.details.map((ligne, i) => <li key={`${i}-${ligne}`}>{ligne}</li>)}
          </ul>
        )}
        <div className="dialog-actions">
          <button type="button" ref={annuler} className="btn" onClick={() => onReponse(false)}>{demande.libelleRefus}</button>
          <button type="button" className="btn accent" onClick={() => onReponse(true)}>{demande.libelleAction}</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
