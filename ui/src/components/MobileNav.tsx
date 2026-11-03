import { useEffect, useId, useRef, useState } from 'react'
import { useLang } from '../lib/langContext'
import { Icon } from './Icon'
import type { IconName } from './Icon'

/**
 * Le bouton « Plus » du dock mobile (retour 15, US4).
 *
 * Le dock garde QUATRE entrées (Transcrire, Bibliothèque, Recherche, Assistant) ; les autres
 * écrans (Voix, Usage, Corbeille, Doublons, Maintenance) s'ouvrent dans la feuille de « Plus » :
 * chaque écran est ainsi à deux touches au plus. Au bureau, CSS cache ce bouton et le dock montre
 * tout, comme avant.
 */
export type EntreePlus = { id: string; label: string; icon: IconName; badge?: number }

export function BoutonPlus({ entrees, courant, surChoisir }: {
  entrees: EntreePlus[]
  courant: string
  surChoisir: (id: string) => void
}) {
  const { t } = useLang()
  const [ouvert, setOuvert] = useState(false)
  const racine = useRef<HTMLDivElement>(null)
  const bouton = useRef<HTMLButtonElement>(null)
  const idFeuille = useId()
  const dedans = entrees.some((e) => e.id === courant)

  useEffect(() => {
    if (!ouvert) return
    const dehors = (e: Event) => {
      if (racine.current && !racine.current.contains(e.target as Node)) setOuvert(false)
    }
    const echap = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setOuvert(false)
      bouton.current?.focus()
    }
    document.addEventListener('mousedown', dehors)
    document.addEventListener('touchstart', dehors)
    document.addEventListener('keydown', echap)
    return () => {
      document.removeEventListener('mousedown', dehors)
      document.removeEventListener('touchstart', dehors)
      document.removeEventListener('keydown', echap)
    }
  }, [ouvert])

  return (
    <div className="dock-plus" ref={racine}>
      <button type="button" ref={bouton} className={`dock-item dock-plus-bouton ${dedans ? 'on' : ''}`}
              aria-expanded={ouvert} aria-controls={idFeuille} onClick={() => setOuvert((o) => !o)}>
        <span className="dock-glyph"><Icon name="more" /></span>
        <span className="dock-label">{t('nav.more')}</span>
      </button>
      {ouvert && (
        <div id={idFeuille} role="group" aria-label={t('nav.more.aria')} className="dock-feuille">
          {entrees.map((e) => (
            <button key={e.id} type="button" className="dock-feuille-item"
                    aria-current={courant === e.id ? 'page' : undefined}
                    onClick={() => { setOuvert(false); surChoisir(e.id) }}>
              <Icon name={e.icon} />
              <span>{e.label}</span>
              {e.badge ? <span className="nav-badge">{e.badge}</span> : null}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
