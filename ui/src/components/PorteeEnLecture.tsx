import type { MouseEvent } from 'react'
import { useLang } from '../lib/langContext'
import { ecrireUrl } from '../lib/urlState'

/**
 * Le lien « Changer dans la Bibliothèque » des écrans qui AFFICHENT la portée sans plus la
 * choisir (Recherche, Assistant, Voix, Usage, Corbeille, Doublons).
 *
 * C'est un vrai lien (adresse copiable) vers le projet consulté ; le clic simple navigue sans
 * recharger. Ces écrans ne portent plus de sélecteur : la consultation se choisit à UN endroit.
 */
export function LienChangerPortee({ viewScope, surChanger }: { viewScope: string; surChanger: () => void }) {
  const { t } = useLang()
  function aller(e: MouseEvent<HTMLAnchorElement>) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    surChanger()
  }
  return (
    <a className="query-scope-change" href={ecrireUrl({ vue: 'library', projet: viewScope })} onClick={aller}>
      {t('projets.scope.change')}
    </a>
  )
}
