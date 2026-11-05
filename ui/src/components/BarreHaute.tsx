import type { FormEvent, MouseEvent, ReactNode } from 'react'
import { useLang } from '../lib/langContext'
import { ecrireUrl } from '../lib/urlState'
import { Icon } from './Icon'
import { MenuReglages } from './MenuReglages'

/**
 * La barre haute allégée : logo, recherche, état des services, indicateur des
 * traitements, Réglages — et rien d'autre. Elle ne porte plus le menu natif « Portée consultée »
 * (la consultation se choisit dans le panneau des projets) ni le partage (dans l'en-tête de projet).
 */
type Props = {
  llm: boolean
  diarisation: boolean
  llmEtat: string
  /** Configuration pas encore reçue : on n'affiche pas de pastilles rouges trompeuses. */
  servicesInconnus?: boolean
  recherche: string
  surRecherche: (saisie: string) => void
  surChangerRecherche: (saisie: string) => void
  surAccueil: () => void
  /** Vrai sur l'écran Recherche : son champ est meilleur que celui-ci, qui ferait doublon. */
  masquerRecherche?: boolean
  /** Emplacement de l'indicateur des traitements. */
  indicateur?: ReactNode
}

export function BarreHaute({ llm, diarisation, llmEtat, servicesInconnus = false, recherche, surRecherche, surChangerRecherche,
                             surAccueil, masquerRecherche = false, indicateur }: Props) {
  const { t } = useLang()

  function accueil(e: MouseEvent<HTMLAnchorElement>) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    surAccueil()
  }

  function soumettre(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    surRecherche(recherche)
  }

  return (
    <header className="contextbar barre-haute">
      <a className="brand" href={ecrireUrl({ vue: 'transcribe' })} onClick={accueil} aria-label={t('barre.home')}>
        <span className="brand-mark" aria-hidden="true">
          <img className="brand-mark-clair" src="/brand/memoglyph-symbole.svg" alt="" />
          <img className="brand-mark-sombre" src="/brand/memoglyph-symbole-sombre.svg" alt="" />
        </span>
        <span>Memoglyph</span>
      </a>

      {!masquerRecherche && (
        <form className="global-search" role="search" onSubmit={soumettre}>
          <button type="submit" aria-label={t('app.search.submit')}><Icon name="search" /></button>
          <input type="search" value={recherche} onChange={(e) => surChangerRecherche(e.target.value)}
                 aria-label={t('app.search.label')} placeholder={t('app.search.placeholder')} />
        </form>
      )}

      <div className="barre-droite">
        {indicateur}
        {!servicesInconnus && (
          <div className="status-dots" aria-label={t('app.status.services')}>
            <span className={llm ? 'dot ok' : 'dot off'} title={llmEtat}>LLM</span>
            <span className={diarisation ? 'dot ok' : 'dot off'} title={t('app.status.diarization')}>Diar</span>
          </div>
        )}
        <MenuReglages />
      </div>
    </header>
  )
}
