import type { ListeTraitements } from '../api'
import { useLang } from '../lib/langContext'
import { comptes } from '../lib/traitements'
import { Icon } from './Icon'

/**
 * L'indicateur discret de la barre haute : « N en cours · M en attente », caché quand rien ne tourne
 * (sauf tant que le panneau est ouvert, pour pouvoir le refermer). Sur mobile, le texte cède la place
 * à une pastille portant le nombre total (CSS).
 */
export function IndicateurTraitements({ liste, ouvert, surBasculer }: {
  liste: ListeTraitements | null
  ouvert: boolean
  surBasculer: () => void
}) {
  const { t } = useLang()
  const { enCours, enAttente, total } = comptes(liste)
  if (total === 0 && !ouvert) return null
  return (
    <button type="button" className={`indicateur-traitements ${ouvert ? 'on' : ''}`}
            aria-expanded={ouvert} aria-haspopup="dialog"
            aria-label={t('traitements.indicator.aria', { enCours, enAttente })}
            onClick={surBasculer}>
      <span className="indicateur-signal" aria-hidden="true"><Icon name="clock" /></span>
      <span className="indicateur-texte">
        {[enCours > 0 && t('traitements.indicator.running', { n: enCours }),
          enAttente > 0 && t('traitements.indicator.waiting', { n: enAttente })].filter(Boolean).join(' · ')
          || t('traitements.panel.title')}
      </span>
      <span className="indicateur-pastille" aria-hidden="true">{total}</span>
    </button>
  )
}
