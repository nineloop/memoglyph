import { useEffect, useState } from 'react'

import { getCloudUltra } from '../api'

/**
 * Le réglage « cloud pour mon ultra-confidentiel » du compte connecté, pour les
 * écrans qui proposent un fournisseur. Lu au montage ; non tant qu'il n'est pas lu,
 * et non si la lecture échoue : en cas de doute, l'écran ne propose que le local.
 * Le serveur reste juge (403) : ce qui est montré ici ne fait qu'éviter un refus.
 */
export const EVENEMENT_CLOUD_ULTRA = 'memoglyph:cloud-ultra'

/** Annonce aux écrans ouverts que le réglage vient de changer (panneau Confidentialité). */
export function annoncerCloudUltra(autorise: boolean): void {
  window.dispatchEvent(new CustomEvent<boolean>(EVENEMENT_CLOUD_ULTRA, { detail: autorise }))
}

export function useCloudUltra(): boolean {
  const [autorise, setAutorise] = useState(false)
  useEffect(() => {
    let actif = true
    getCloudUltra()
      .then((reglage) => { if (actif && reglage?.autorise === true) setAutorise(true) })
      .catch(() => {})
    const ecouter = (event: Event) => setAutorise((event as CustomEvent<boolean>).detail === true)
    window.addEventListener(EVENEMENT_CLOUD_ULTRA, ecouter)
    return () => { actif = false; window.removeEventListener(EVENEMENT_CLOUD_ULTRA, ecouter) }
  }, [])
  return autorise
}
