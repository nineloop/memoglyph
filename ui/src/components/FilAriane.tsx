import type { MouseEvent } from 'react'
import { useLang } from '../lib/langContext'
import { ecrireUrl } from '../lib/urlState'

/**
 * Le fil d'Ariane : Bibliothèque > projet > réunion.
 *
 * Chaque niveau cliquable est un vrai lien (adresse copiable) dont le clic simple navigue sans
 * recharger. Le DERNIER niveau est la page courante : un texte marqué `aria-current`, pas un lien.
 */
type Props = {
  projet?: { id: string; nom: string }
  reunion?: string
  surBibliotheque: () => void
  surProjet: () => void
}

function aller(sur: () => void) {
  return (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    sur()
  }
}

export function FilAriane({ projet, reunion, surBibliotheque, surProjet }: Props) {
  const { t } = useLang()
  const niveaux: { cle: string; texte: string; href?: string; sur?: () => void }[] = [
    { cle: 'biblio', texte: t('nav.crumb.library'), href: ecrireUrl({ vue: 'library', projet: 'all' }), sur: surBibliotheque },
  ]
  if (projet) niveaux.push({ cle: 'projet', texte: projet.nom, href: ecrireUrl({ vue: 'library', projet: projet.id }), sur: surProjet })
  if (reunion) niveaux.push({ cle: 'reunion', texte: reunion })
  return (
    <nav className="fil-ariane" aria-label={t('nav.crumb.aria')}>
      <ol>
        {niveaux.map((n, i) => {
          const dernier = i === niveaux.length - 1
          return (
            <li key={n.cle}>
              {dernier
                ? <span aria-current="page" className="fil-courant">{n.texte}</span>
                : <a href={n.href} onClick={aller(n.sur!)}>{n.texte}</a>}
              {/* Le séparateur suit son élément : à la coupure de ligne il reste en fin de ligne, jamais en tête. */}
              {!dernier && <span className="fil-sep" aria-hidden="true">›</span>}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
