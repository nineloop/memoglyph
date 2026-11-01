import type { LibraryItem } from '../api'
import { pluralKey, translate, type Traduire } from './i18n'

/**
 * Réserves de qualité, dans la langue de l'interface.
 *
 * Les clés viennent du front-matter, écrites par le pipeline (transcript) et par
 * le générateur de compte-rendu. Elles ne portent PAS sur le même objet :
 * `unsupported_numbers` accuse le CR, `repetition_loops` accuse le transcript.
 * Les afficher indistinctement ferait mentir les deux — d'où le champ `objet`.
 */
export type Reserve = { objet: string; texte: string; detail: string[] }

// Compteurs : une liste par clé du front-matter, libellé accordé `reserve.<clé>.one/other`.
const COMPTEURS = [
  'repetition_loops', 'removed_credits', 'repeated_series', 'map_chunks_failed',
  'removed_speaker_bindings', 'unsupported_numbers', 'citation_warnings',
] as const

const francais: Traduire = (cle, params) => translate('fr', cle, params)

/** Lit les réserves d'un document depuis son front-matter déjà chargé. Sans `t`, en français. */
export function reservesDe(objet: string, meta: Record<string, unknown>, t: Traduire = francais): Reserve[] {
  const out: Reserve[] = []
  for (const cle of COMPTEURS) {
    const v = meta[cle]
    if (!Array.isArray(v) || v.length === 0) continue
    out.push({ objet, texte: t(pluralKey(`reserve.${cle}`, v.length), { n: v.length }), detail: v.map(String) })
  }
  // Une diarisation manquée laisse les locuteurs inconnus : la réserve en donne la cause.
  const diarisation = meta.diarization_error
  if (typeof diarisation === 'string' && diarisation.trim()) {
    out.push({ objet, texte: t('reserve.diarization_error'), detail: [diarisation.trim()] })
  }
  return out
}

/** Compte porté par la ligne de bibliothèque — l'index l'a déjà calculé. */
export function compteReserves(items: LibraryItem[]): number {
  return items.reduce((n, i) => n + (i.warnings || 0), 0)
}
