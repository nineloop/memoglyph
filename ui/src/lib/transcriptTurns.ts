/**
 * Le transcript, découpé en tours de parole.
 *
 * Pourquoi : le document le plus consulté de l'application
 * était rendu en une seule `<pre>` monospace, où `**Speaker 1**` se répétait
 * trois cents fois. Ce module en tire la structure que le générateur y a déjà
 * mise, sans rien inventer.
 *
 * ## Le format vient de `core/writer_md.py`
 *
 * Trois formes de corps y sont écrites (`render_markdown`) :
 *
 * - diarisé et horodaté  → `**Speaker 1** [00:12:34] texte`
 * - horodaté seul        → `[00:12:34] texte`
 * - ni l'un ni l'autre   → un paragraphe continu
 *
 * Un transcript importé d'ailleurs est lu comme le lit le compte rendu
 * (`core/experimental/chunking.py`, `lire_repliques`) : horodatage court
 * `[MM:SS]`, locuteur hors gras avant l'horodatage (« Nom [00:12:34] texte »),
 * ou après lui (« [00:12:34] Nom : texte »). Les mêmes cas sont testés des deux
 * côtés (`test/transcriptTurns.test.ts`, `tests/unit/test_formats_transcript.py`).
 *
 * ## L'horodatage affiché est celui du compte rendu
 *
 * `[75:10]` compte des minutes : il s'affiche `[01:15:10]`, la forme que citent
 * le compte rendu et les faits. `[HH:MM:SS]` reste tel qu'écrit.
 *
 * ## « Nom : » ne fait un locuteur que s'il revient
 *
 * « [00:01:02] Budget : 25 € » isolée est une réplique sans locuteur, texte
 * entier. Le nom devient locuteur seulement s'il ouvre au moins deux répliques
 * sans locuteur du transcript, et s'il tient en quatre mots sans chiffre : la
 * lecture se fait donc en deux temps, comme côté serveur.
 *
 * ## Un tour EXIGE un horodatage
 *
 * Ce n'est pas une commodité de parsing, c'est une garde. Le profil LLM qui
 * précède le transcript écrit lui aussi des lignes en gras — `**Type :**
 * conversation`, `**Speaker 1 — Animateur** · 12:34 · 45 %`. Sans l'exigence
 * de crochets, ces lignes deviendraient des tours de parole fantômes, avec un
 * locuteur nommé « Type : ».
 *
 * ## Aucune ligne ne se perd
 *
 * Une ligne qui ne s'analyse pas est rattachée au passage précédent plutôt que
 * jetée. Un transcript retouché à la main reste lisible en entier, et
 * `test/transcriptTurns.test.ts` le vérifie ligne à ligne.
 */

/** Un passage : une prise de parole horodatée. */
export type Passage = {
  /** L'horodatage en forme `[HH:MM:SS]` — « [00:12:34] », « [75:10] » devenu « [01:15:10] ». */
  mark: string
  /** Sa valeur en secondes, pour le lecteur audio. */
  at: number
  texte: string
}

/** Un tour : les passages CONSÉCUTIFS d'un même locuteur. */
export type Tour = {
  /** `null` sur un transcript horodaté mais non diarisé. */
  locuteur: string | null
  passages: Passage[]
}

export type Lecture = {
  /** Ce qui précède le premier tour — titre, profil LLM. Markdown brut. */
  preambule: string
  tours: Tour[]
}

// `**Locuteur** [hh:]mm:ss texte` — le locuteur ne peut pas contenir d'astérisque.
const TOUR_DIARISE = /^\*\*([^*]+)\*\*\s+(\[(\d{1,2}):(\d{2})(?::(\d{2}))?\])\s*(.*)$/
// `[hh:]mm:ss texte` — même document, sans diarisation.
const TOUR_NU = /^(\[(\d{1,2}):(\d{2})(?::(\d{2}))?\])\s*(.*)$/
// Un nom hors gras : un à quatre mots, sans chiffre ; un mot commence par une
// lettre et ne contient que lettres, apostrophes, points et traits d'union.
const MOT = String.raw`\p{L}(?:\p{L}|['’.\-])*`
const NOM = String.raw`${MOT}(?: ${MOT}){0,3}`
// `Nom [hh:]mm:ss texte` — locuteur hors gras, avant l'horodatage.
const TOUR_NOM_AVANT = new RegExp(String.raw`^(${NOM})\s+(\[(\d{1,2}):(\d{2})(?::(\d{2}))?\])\s*(.*)$`, 'u')
// `Nom : texte` au début du texte d'une réplique sans locuteur.
const NOM_DEUX_POINTS = new RegExp(String.raw`^(${NOM})\s*:\s+(\S.*)$`, 'u')
// Nombre de répliques qu'un « Nom : » doit ouvrir pour devenir un locuteur.
const REPETITIONS_LOCUTEUR = 2

function secondes(a: string, b: string, c?: string): number {
  return c === undefined
    ? Number(a) * 60 + Number(b)
    : Number(a) * 3600 + Number(b) * 60 + Number(c)
}

const deux = (n: number) => String(n).padStart(2, '0')

/** `[HH:MM:SS]` ; `[HH:MM:SS]` déjà écrit, ou un horodatage impossible, reste tel quel. */
function marque(ecrite: string, a: string, b: string, c?: string): string {
  if (c !== undefined && a.length === 2) return ecrite
  const [h, m, s] = c === undefined
    ? [Math.floor(Number(a) / 60), Number(a) % 60, Number(b)]
    : [Number(a), Number(b), Number(c)]
  if (s >= 60 || m >= 60) return ecrite
  return `[${deux(h)}:${deux(m)}:${deux(s)}]`
}

type Analyse = { locuteur: string | null; mark: string; at: number; texte: string }

/** Une ligne, sans la règle « Nom : » ; `nomAvant` : lire aussi `Nom [hh:mm:ss]`. */
function analyserBrut(ligne: string, nomAvant: boolean): Analyse | null {
  const diarise = TOUR_DIARISE.exec(ligne)
  if (diarise) {
    const [, locuteur, mark, h, m, s, texte] = diarise
    return { locuteur: locuteur.trim(), mark: marque(mark, h, m, s), at: secondes(h, m, s), texte }
  }
  const nu = TOUR_NU.exec(ligne)
  if (nu) {
    const [, mark, h, m, s, texte] = nu
    return { locuteur: null, mark: marque(mark, h, m, s), at: secondes(h, m, s), texte }
  }
  const avant = nomAvant ? TOUR_NOM_AVANT.exec(ligne) : null
  if (avant) {
    const [, locuteur, mark, h, m, s, texte] = avant
    return { locuteur, mark: marque(mark, h, m, s), at: secondes(h, m, s), texte }
  }
  return null
}

/** Le nom qui ouvre le texte d'une réplique sans locuteur, ou null. */
function nomOuvrant(analyse: Analyse): RegExpExecArray | null {
  return analyse.locuteur === null ? NOM_DEUX_POINTS.exec(analyse.texte.trim()) : null
}

export function decouperEnTours(corps: string): Lecture {
  const lignes = corps.replace(/\r\n?/g, '\n').split('\n')

  // Les formes importées (nom hors gras, « Nom : ») ne se lisent que dans la
  // section « ## Transcript » quand elle existe : le profil qui la précède écrit
  // du texte libre, borné par rien. Les formes de `writer_md` gardent leur lecture.
  const section = lignes.findIndex((l) => l.startsWith('## Transcript'))
  const nomAvant = (i: number) => i > section

  // Premier temps : les noms qui ouvrent au moins deux répliques sans locuteur.
  const ouvertures = new Map<string, number>()
  lignes.forEach((ligne, i) => {
    const brut = nomAvant(i) ? analyserBrut(ligne, true) : null
    const nom = brut && brut.texte.trim() ? nomOuvrant(brut)?.[1] : undefined
    if (nom) ouvertures.set(nom, (ouvertures.get(nom) ?? 0) + 1)
  })
  const analyser = (ligne: string, i: number): Analyse | null => {
    const brut = analyserBrut(ligne, nomAvant(i))
    if (!brut || !nomAvant(i)) return brut
    const ouvrant = nomOuvrant(brut)
    if (ouvrant && (ouvertures.get(ouvrant[1]) ?? 0) >= REPETITIONS_LOCUTEUR) {
      return { ...brut, locuteur: ouvrant[1], texte: ouvrant[2] }
    }
    return brut
  }

  // Le préambule court jusqu'au PREMIER tour. Tant qu'aucun n'a été vu, une
  // ligne appartient à l'en-tête du document, pas à la conversation.
  let debut = -1
  for (let i = 0; i < lignes.length; i++) {
    if (analyser(lignes[i], i)) { debut = i; break }
  }
  if (debut < 0) return { preambule: corps, tours: [] }

  const preambule = lignes.slice(0, debut).join('\n').trim()
  const tours: Tour[] = []

  for (let i = debut; i < lignes.length; i++) {
    const ligne = lignes[i]
    const analyse = analyser(ligne, i)
    if (analyse) {
      const dernier = tours[tours.length - 1]
      const passage: Passage = { mark: analyse.mark, at: analyse.at, texte: analyse.texte.trim() }
      // Regrouper les passages consécutifs d'un même locuteur : c'est le seul
      // endroit où la lecture gagne vraiment, le générateur répétant le nom à
      // chaque segment.
      if (dernier && dernier.locuteur === analyse.locuteur) dernier.passages.push(passage)
      else tours.push({ locuteur: analyse.locuteur, passages: [passage] })
      continue
    }
    // Ligne non analysable APRÈS le premier tour : suite du passage courant.
    // La jeter serait perdre du texte retouché à la main.
    const vide = ligne.trim() === ''
    if (vide) continue
    const dernier = tours[tours.length - 1]
    const passage = dernier?.passages[dernier.passages.length - 1]
    if (passage) passage.texte = `${passage.texte}\n${ligne.trim()}`.trim()
  }

  return { preambule, tours }
}

/** Les locuteurs, dans leur ordre d'apparition, avec leur nombre de passages. */
export function locuteursDe(tours: Tour[]): { nom: string; passages: number }[] {
  const compte = new Map<string, number>()
  for (const tour of tours) {
    if (!tour.locuteur) continue
    compte.set(tour.locuteur, (compte.get(tour.locuteur) ?? 0) + tour.passages.length)
  }
  return [...compte].map(([nom, passages]) => ({ nom, passages }))
}

/**
 * Des repères réguliers pour naviguer — un par tranche de `pas` secondes.
 *
 * Pas un sommaire : le document n'a pas de chapitres. Une réunion d'une heure
 * donne douze points d'accroche, ce qu'aucune barre de défilement ne fournit.
 */
export function reperesDe(tours: Tour[], pas = 300): { mark: string; at: number }[] {
  const reperes: { mark: string; at: number }[] = []
  let prochain = 0
  for (const tour of tours) {
    for (const passage of tour.passages) {
      if (passage.at < prochain) continue
      reperes.push({ mark: passage.mark, at: passage.at })
      // Le pas suit le passage RETENU, pas l'horloge : un silence de vingt
      // minutes ne doit pas produire quatre repères sur le même passage.
      prochain = passage.at + pas
    }
  }
  return reperes
}
