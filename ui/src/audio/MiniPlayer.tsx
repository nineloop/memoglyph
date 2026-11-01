import { useEffect } from 'react'
import { useAudio } from './AudioProvider'
import { Icon } from '../components/Icon'
import { useLang } from '../lib/langContext'
import './audio.css'

/** mm:ss à partir de secondes (— si inconnu). */
function fmt(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  const s = Math.floor(seconds)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/**
 * Mini-lecteur « en cours d'écoute », persistant en bas d'écran. Le vrai
 * défileur est un `<input type="range">` — accessible
 * au clavier, honnête : il reflète la position réelle du média.
 */
export function MiniPlayer() {
  const { track, playing, currentTime, duration, toggle, seek, close } = useAudio()
  const { t } = useLang()
  // Visible, il flotte au-dessus du dock : il marque la racine, et la mise en page
  // réserve sa place sous le contenu (`:root[data-lecteur] .app-main`).
  const visible = track !== null
  useEffect(() => {
    if (!visible) return
    document.documentElement.dataset.lecteur = ''
    return () => { delete document.documentElement.dataset.lecteur }
  }, [visible])
  if (!track) return null
  return (
    <section className="mini-player" aria-label={t('player.aria')}>
      <button type="button" className="mini-play" onClick={toggle}
              aria-label={playing ? t('player.pause') : t('player.resume')}>
        <Icon name={playing ? 'pause' : 'play'} />
      </button>
      <div className="mini-info">
        <span className="mini-eyebrow">{t('player.eyebrow')}</span>
        <span className="mini-title" title={track.title}>{track.title}</span>
      </div>
      <div className="mini-scrub">
        <span className="mini-time">{fmt(currentTime)}</span>
        <input
          type="range" className="mini-range" min={0} max={Math.max(1, Math.floor(duration))}
          value={Math.min(currentTime, Math.floor(duration) || currentTime)}
          onChange={(e) => seek(Number(e.target.value))}
          aria-label={t('player.position')}
          style={{ ['--pct' as string]: `${duration ? (currentTime / duration) * 100 : 0}%` }}
        />
        <span className="mini-time dim">{fmt(duration)}</span>
      </div>
      <button type="button" className="mini-close" onClick={close} aria-label={t('player.close')}>
        <Icon name="close" />
      </button>
    </section>
  )
}
