import { useEffect, useState } from 'react'

/**
 * Forme d'onde RÉELLE, décodée depuis l'audio conservé — pas un motif inventé.
 *
 * L'interface fait de l'audio l'objet central : l'écran d'écoute
 * montre une onde pleine largeur. Pour rester honnête (aucune donnée simulée),
 * on décode réellement le fichier via Web Audio et on sous-échantillonne les
 * crêtes. Quand ce n'est pas possible — API absente (jsdom), fichier trop lourd,
 * décodage refusé — on l'annonce par un `state` explicite plutôt que d'afficher
 * une onde décorative qui mentirait sur le signal.
 */
export type EtatOnde = 'idle' | 'loading' | 'ready' | 'error' | 'too-large' | 'unsupported'

const BARRES = 320
// Au-delà, le téléchargement + décodage en mémoire coûte trop cher pour un
// simple aperçu : on préfère une frise neutre à un onglet qui rame.
const MAX_OCTETS = 90 * 1024 * 1024
// Les crêtes sont stables pour une URL donnée : on évite de re-décoder à chaque
// ré-ouverture du même document dans la même session.
const cache = new Map<string, Float32Array>()

type Fenetre = typeof window & { webkitAudioContext?: typeof AudioContext }

function reduire(canal: Float32Array): Float32Array {
  const bloc = Math.floor(canal.length / BARRES) || 1
  const out = new Float32Array(BARRES)
  let max = 0
  for (let i = 0; i < BARRES; i++) {
    let crete = 0
    const debut = i * bloc
    for (let j = 0; j < bloc; j++) {
      const v = Math.abs(canal[debut + j] ?? 0)
      if (v > crete) crete = v
    }
    out[i] = crete
    if (crete > max) max = crete
  }
  // Normalise pour que le passage le plus fort remplisse la hauteur.
  if (max > 0) for (let i = 0; i < BARRES; i++) out[i] /= max
  return out
}

export function useWaveform(url: string | null, actif: boolean): {
  cretes: Float32Array | null
  state: EtatOnde
} {
  const [cretes, setCretes] = useState<Float32Array | null>(null)
  const [state, setState] = useState<EtatOnde>('idle')

  useEffect(() => {
    if (!actif || !url) { setState('idle'); setCretes(null); return }
    const enCache = cache.get(url)
    if (enCache) { setCretes(enCache); setState('ready'); return }

    const AC = window.AudioContext ?? (window as Fenetre).webkitAudioContext
    if (!AC) { setState('unsupported'); setCretes(null); return }

    let annule = false
    setState('loading'); setCretes(null)
    ;(async () => {
      let ctx: AudioContext | null = null
      try {
        const res = await fetch(url)
        if (!res.ok) throw new Error(String(res.status))
        const annonce = Number(res.headers.get('content-length') ?? 0)
        if (annonce && annonce > MAX_OCTETS) { if (!annule) setState('too-large'); return }
        const buf = await res.arrayBuffer()
        if (buf.byteLength > MAX_OCTETS) { if (!annule) setState('too-large'); return }
        ctx = new AC()
        const audio = await ctx.decodeAudioData(buf)
        const reduites = reduire(audio.getChannelData(0))
        cache.set(url, reduites)
        if (!annule) { setCretes(reduites); setState('ready') }
      } catch {
        if (!annule) setState('error')
      } finally {
        ctx?.close?.()
      }
    })()
    return () => { annule = true }
  }, [url, actif])

  return { cretes, state }
}
