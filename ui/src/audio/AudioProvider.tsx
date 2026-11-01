import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react'
import type { ReactNode } from 'react'
import { audioUrl } from '../api'
import { ecrireCle, lireCle, retirerCle } from '../lib/stockage'

/**
 * L'audio est l'objet CENTRAL du produit.
 *
 * Un seul élément `<audio>` vit ici, à la racine, et SURVIT aux changements de
 * vue : c'est ce qui permet un mini-lecteur « en cours d'écoute » persistant et
 * une reprise à la position sauvegardée. La position est mémorisée PAR DOCUMENT
 * dans `localStorage` — état honnête côté client, aucune capacité backend
 * simulée (le backend ne stocke pas de position de lecture).
 */
export type Track = {
  name: string
  scope: string
  title: string
  meetingId?: string | null
}

type AudioApi = {
  track: Track | null
  playing: boolean
  /** Seconde courante, ARRONDIE (évite un re-render 4×/s de toute l'app). */
  currentTime: number
  duration: number
  /** Ouvre un document dans le lecteur. Sans `startAt`, reprend la position sauvegardée. */
  play: (track: Track, opts?: { startAt?: number; autoplay?: boolean }) => void
  /** Se place à une seconde du document courant ; `autoplay` pour écouter. */
  seek: (seconds: number, opts?: { autoplay?: boolean }) => void
  toggle: () => void
  close: () => void
  /** Position sauvegardée d'un document (0 si aucune), pour les affordances « reprendre ». */
  savedPosition: (name: string) => number
}

const Ctx = createContext<AudioApi | null>(null)
const posKey = (name: string) => `pos:${name}`
// Dernière écoute, TOUS documents confondus : permet l'accueil « reprendre »
// de la bibliothèque sans qu'aucun lecteur ne soit encore monté.
const RECENT = 'recent'

export function lirePosition(name: string): number {
  try {
    const v = lireCle(posKey(name))
    const n = v ? Number(v) : 0
    return Number.isFinite(n) && n > 1 ? n : 0
  } catch { return 0 }
}
function ecrirePosition(name: string, seconds: number) {
  try {
    if (seconds > 1) ecrireCle(posKey(name), String(Math.floor(seconds)))
  } catch { /* stockage indisponible : sans effet */ }
}

export type DerniereEcoute = { name: string; scope: string; title: string; at: number }

/** Le dernier document écouté et sa position — état honnête côté client. */
export function derniereEcoute(): DerniereEcoute | null {
  try {
    const brut = lireCle(RECENT)
    if (!brut) return null
    const v = JSON.parse(brut) as Partial<DerniereEcoute>
    if (!v.name || !v.title || typeof v.at !== 'number' || !(v.at > 1)) return null
    return { name: v.name, scope: v.scope ?? '', title: v.title, at: Math.floor(v.at) }
  } catch { return null }
}
function ecrireRecent(track: Track, seconds: number) {
  try {
    if (seconds > 1) {
      ecrireCle(RECENT, JSON.stringify(
        { name: track.name, scope: track.scope, title: track.title, at: Math.floor(seconds) },
      ))
    }
  } catch { /* stockage indisponible : sans effet */ }
}
function oublierRecent(name: string) {
  try {
    const actuel = derniereEcoute()
    if (actuel?.name === name) retirerCle(RECENT)
  } catch { /* sans effet */ }
}

function jouerSansPromesse(el: HTMLAudioElement) {
  try {
    const p = el.play()
    if (p && typeof (p as Promise<void>).catch === 'function') (p as Promise<void>).catch(() => {})
  } catch { /* jsdom / autoplay bloqué : sans effet */ }
}

export function AudioProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [track, setTrack] = useState<Track | null>(null)
  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  // Ce qu'on veut faire dès que les métadonnées du média sont prêtes.
  const enAttente = useRef<{ startAt?: number; autoplay?: boolean } | null>(null)

  const play = useCallback((next: Track, opts?: { startAt?: number; autoplay?: boolean }) => {
    setTrack((prev) => {
      if (prev?.name === next.name && prev?.scope === next.scope) {
        // Déjà chargé : on se contente de (re)positionner.
        const el = audioRef.current
        if (el) {
          const cible = opts?.startAt ?? el.currentTime
          el.currentTime = cible
          setCurrentTime(Math.floor(cible))
          enAttente.current = null // position explicite : plus rien à appliquer aux métadonnées
          if (opts?.autoplay) jouerSansPromesse(el)
        }
        return prev
      }
      enAttente.current = { startAt: opts?.startAt, autoplay: opts?.autoplay }
      setCurrentTime(0); setDuration(0)
      return next
    })
  }, [])

  const seek = useCallback((seconds: number, opts?: { autoplay?: boolean }) => {
    const el = audioRef.current
    if (!el) return
    // Une position EXPLICITE (citation d'un compte-rendu, clic sur la frise) est
    // une INTENTION, pas seulement un `currentTime=` immédiat : on la mémorise
    // donc comme la reprise en attente, au lieu de l'effacer. Sans quoi, si
    // l'élément (re)charge juste après — retour à la source qui rouvre un autre
    // document, readyState retombe à 0 et le navigateur IGNORE le `currentTime=`
    // —, `onLoadedMetadata` restaurerait l'ancienne position sauvegardée et
    // écraserait la citation. En la gardant dans `enAttente`, c'est la citation
    // qui est rejouée au chargement. Elle est consommée (remise à null) dès que
    // les métadonnées l'appliquent.
    enAttente.current = { startAt: seconds, autoplay: opts?.autoplay }
    el.currentTime = seconds
    setCurrentTime(Math.floor(seconds))
    if (opts?.autoplay) jouerSansPromesse(el)
  }, [])

  const toggle = useCallback(() => {
    const el = audioRef.current
    if (!el) return
    if (el.paused) jouerSansPromesse(el)
    else el.pause()
  }, [])

  const close = useCallback(() => {
    const el = audioRef.current
    if (el && track) { ecrirePosition(track.name, el.currentTime); ecrireRecent(track, el.currentTime) }
    if (el) el.pause()
    setTrack(null); setPlaying(false); setCurrentTime(0); setDuration(0)
  }, [track])

  const savedPosition = useCallback((name: string) => lirePosition(name), [])

  // La durée peut se préciser en plusieurs fois (`durationchange` se répète) :
  // on la met à jour à chaque fois, mais SANS toucher à la tête de lecture.
  const onDurationChange = useCallback(() => {
    const el = audioRef.current
    if (el) setDuration(Number.isFinite(el.duration) ? el.duration : 0)
  }, [])

  // Métadonnées prêtes → durée, puis reprise/positionnement demandé. Le
  // positionnement vit ICI, sur `loadedmetadata` (émis UNE fois par chargement),
  // et PAS sur `durationchange` : autrement chaque `durationchange` rejouerait la
  // reprise sauvegardée et ramènerait la tête en arrière — ce qui écrasait une
  // citation déjà appliquée (retour à la source).
  const onLoadedMetadata = useCallback(() => {
    const el = audioRef.current
    if (!el || !track) return
    setDuration(Number.isFinite(el.duration) ? el.duration : 0)
    const demande = enAttente.current
    enAttente.current = null
    const startAt = demande?.startAt ?? lirePosition(track.name)
    if (startAt > 0) { el.currentTime = startAt; setCurrentTime(Math.floor(startAt)) }
    if (demande?.autoplay) jouerSansPromesse(el)
  }, [track])

  const onTimeUpdate = useCallback(() => {
    const el = audioRef.current
    if (!el) return
    const sec = Math.floor(el.currentTime)
    setCurrentTime((prev) => (prev === sec ? prev : sec))
    if (track) { ecrirePosition(track.name, el.currentTime); ecrireRecent(track, el.currentTime) }
  }, [track])

  // Sauvegarde de sûreté quand l'onglet se ferme en pleine écoute.
  useEffect(() => {
    const sauver = () => {
      const el = audioRef.current
      if (el && track) { ecrirePosition(track.name, el.currentTime); ecrireRecent(track, el.currentTime) }
    }
    addEventListener('pagehide', sauver)
    return () => removeEventListener('pagehide', sauver)
  }, [track])

  const value = useMemo<AudioApi>(() => ({
    track, playing, currentTime, duration, play, seek, toggle, close, savedPosition,
  }), [track, playing, currentTime, duration, play, seek, toggle, close, savedPosition])

  return (
    <Ctx.Provider value={value}>
      {children}
      {track && (
        <audio
          ref={audioRef}
          src={audioUrl(track.name, track.scope)}
          preload="metadata"
          onLoadedMetadata={onLoadedMetadata}
          onTimeUpdate={onTimeUpdate}
          onDurationChange={onDurationChange}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => { setPlaying(false); if (track) { ecrirePosition(track.name, 0); oublierRecent(track.name) } }}
        />
      )}
    </Ctx.Provider>
  )
}

/**
 * L'audio est une COUCHE, pas une dépendance dure : sans provider (montage isolé
 * d'un composant en test, par ex.), on rend un stub inerte plutôt que de planter.
 * L'application réelle monte toujours l'`AudioProvider` à la racine.
 */
const STUB: AudioApi = Object.freeze({
  track: null, playing: false, currentTime: -1, duration: 0,
  play: () => {}, seek: () => {}, toggle: () => {}, close: () => {}, savedPosition: () => 0,
})

export function useAudio(): AudioApi {
  return useContext(Ctx) ?? STUB
}
