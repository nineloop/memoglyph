import { useRef } from 'react'
import { useWaveform } from './useWaveform'
import { useLang } from '../lib/langContext'
import { pluralKey } from '../lib/i18n'

export type Chapitre = { ratio: number; mark: string; label?: string }

type Props = {
  /** Source à décoder (l'audio conservé du document). */
  url: string | null
  /** N'engage le décodage que quand l'écran d'écoute est réellement visible. */
  actif: boolean
  /** Avancement 0…1 pour le remplissage et la tête de lecture. */
  progress: number
  /** Seconde courante et durée, pour l'accessibilité et le pas clavier. */
  seconds: number
  duration: number
  chapters: Chapitre[]
  /** Se placer à une fraction 0…1 de l'enregistrement (clic ou flèches). */
  onScrub: (ratio: number) => void
}

const LARGEUR = 320
const HAUTEUR = 64

function mmss(total: number): string {
  if (!Number.isFinite(total) || total < 0) return '0:00'
  const s = Math.floor(total % 60)
  const m = Math.floor(total / 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

/**
 * Frise d'écoute pleine largeur — la « une » de l'objet audio. Les crêtes
 * sont RÉELLES quand le décodage aboutit ; sinon la frise
 * reste une timeline neutre, toujours cliquable, et dit franchement pourquoi
 * l'onde manque. On ne dessine jamais de fausse onde.
 */
export function Waveform({ url, actif, progress, seconds, duration, chapters, onScrub }: Props) {
  const { cretes, state } = useWaveform(actif ? url : null, actif)
  const { t } = useLang()
  const zone = useRef<HTMLDivElement | null>(null)
  const p = Math.max(0, Math.min(1, progress))

  function ratioDepuisX(clientX: number): number {
    const rect = zone.current?.getBoundingClientRect()
    if (!rect || rect.width === 0) return 0
    return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (duration <= 0) return
    const pas = 5 / duration // ~5 s
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { e.preventDefault(); onScrub(Math.min(1, p + pas)) }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { e.preventDefault(); onScrub(Math.max(0, p - pas)) }
    else if (e.key === 'Home') { e.preventDefault(); onScrub(0) }
    else if (e.key === 'End') { e.preventDefault(); onScrub(1) }
  }

  const message: Record<string, string> = {
    loading: t('wave.loading'),
    'too-large': t('wave.tooLarge'),
    unsupported: t('wave.unsupported'),
    error: t('wave.error'),
  }

  return (
    <div className="ecoute">
      <div
        ref={zone}
        className={`wave wave--${state}`}
        role="slider"
        tabIndex={0}
        aria-label={t('wave.position.aria')}
        aria-valuemin={0}
        aria-valuemax={Math.round(duration) || 0}
        aria-valuenow={Math.round(seconds) || 0}
        aria-valuetext={t('wave.valueText', { position: mmss(seconds), duree: mmss(duration) })}
        onKeyDown={onKeyDown}
        onClick={(e) => onScrub(ratioDepuisX(e.clientX))}
      >
        <svg viewBox={`0 0 ${LARGEUR} ${HAUTEUR}`} preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <linearGradient id="wave-grad" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="var(--kf-vif)" />
              <stop offset="1" stopColor="var(--kf-menthe)" />
            </linearGradient>
          </defs>
          {cretes
            ? Array.from(cretes, (crete, i) => {
                const h = Math.max(1.6, crete * (HAUTEUR - 6))
                const joue = (i + 1) / cretes.length <= p
                return (
                  <rect
                    key={i}
                    className={joue ? 'wbar on' : 'wbar'}
                    x={(i / cretes.length) * LARGEUR + 0.25}
                    width={LARGEUR / cretes.length - 0.5}
                    y={(HAUTEUR - h) / 2}
                    height={h}
                    rx={0.6}
                  />
                )
              })
            : (
              <>
                <rect className="wbar" x={0} y={HAUTEUR / 2 - 2} width={LARGEUR} height={4} rx={2} />
                <rect className="wbar on" x={0} y={HAUTEUR / 2 - 2} width={LARGEUR * p} height={4} rx={2} />
              </>
            )}
          {chapters.map((c) => (
            <rect key={c.mark} className="wtick" x={c.ratio * LARGEUR} y={2} width={0.8} height={HAUTEUR - 4} />
          ))}
          <rect className="whead" x={Math.max(0, p * LARGEUR - 0.6)} y={0} width={1.2} height={HAUTEUR} />
        </svg>
      </div>

      <div className="wave-legende">
        <span className="wave-temps">{mmss(seconds)}</span>
        {state !== 'ready' && state !== 'idle' && (
          <span className="wave-note">{message[state]}</span>
        )}
        {state === 'ready' && chapters.length > 0 && (
          <span className="wave-note">{t(pluralKey('wave.markers', chapters.length), { n: chapters.length })}</span>
        )}
        <span className="wave-temps dim">{mmss(duration)}</span>
      </div>
    </div>
  )
}
