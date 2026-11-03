import { useLang } from '../lib/langContext'
import { Icon } from './Icon'

type Voisine = { cle: string; titre: string }

/** Les flèches « réunion précédente » et « suivante » du même projet ; inactives aux extrémités. */
export function NavigationReunion({ precedente, suivante, surAller }: {
  precedente?: Voisine
  suivante?: Voisine
  surAller: (cle: string) => void
}) {
  const { t } = useLang()
  return (
    <div className="navigation-reunion">
      <button type="button" className="icon-btn" disabled={!precedente}
              aria-label={t('nav.meeting.previous')} title={precedente?.titre ?? t('nav.meeting.previous')}
              onClick={() => precedente && surAller(precedente.cle)}>
        <Icon name="arrow-left" />
      </button>
      <button type="button" className="icon-btn" disabled={!suivante}
              aria-label={t('nav.meeting.next')} title={suivante?.titre ?? t('nav.meeting.next')}
              onClick={() => suivante && surAller(suivante.cle)}>
        <span className="fleche-droite"><Icon name="arrow-left" /></span>
      </button>
    </div>
  )
}
