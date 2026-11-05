import type { LibraryItem, Meeting } from '../api'

/**
 * Regroupement des documents en RÉUNIONS.
 *
 * La bibliothèque affichait 32 documents à plat — transcripts et comptes-rendus
 * au même niveau, comme des frères. Regroupés, ils font 17 réunions.
 *
 * ## La clé est `meeting_id`
 *
 * `meeting_id` vit dans le Markdown et survit aux renommages. `source` ne sert
 * plus que de repli transitoire pour un ancien document non migré.
 *
 * `source_transcript` ne conviendrait pas : il relie un compte-rendu à SON
 * transcript, mais ne relie pas deux transcripts entre eux — les versions
 * resteraient des lignes séparées.
 *
 * ## Regroupement EXACT, jamais par similarité
 *
 * On ne compare jamais des contenus ici. `core/dedup.py` sait le faire, et
 * l'onglet Doublons est fait pour ça : l'utilisateur y arbitre une fusion
 * proposée. Dans la NAVIGATION, un faux positif ferait disparaître un document
 * derrière un mauvais parent — sans que personne ne le sache.
 *
 * Conséquence assumée, visible sur le corpus : un transcript ancien déclare
 * `source: …copy.m4a` là où ses frères déclarent le `.mp4`. Deux lignes pour ce
 * qui est humainement un seul entretien. Mieux vaut sous-grouper visiblement que
 * sur-grouper à tort.
 */
export type Reunion = {
  /** Clé interne stable — `meeting_id`, ou clé legacy en repli. */
  cle: string
  /** Média source affichable ; jamais utilisé comme identité. */
  source: string | null
  titre: string
  /** Date la plus ANCIENNE du groupe : celle de la réunion, pas du dernier retraitement. */
  date: string
  duree: string | null
  langue: string | null
  projet: string
  nSpeakers: number | null
  /** Somme des réserves de qualité de tous les éléments du groupe. */
  avertissements: number
  /** Transcripts du groupe, le retenu en tête. */
  transcripts: LibraryItem[]
  /** Comptes-rendus rattachés, plus récent en tête. */
  comptesRendus: LibraryItem[]
  /** Le transcript à afficher par défaut. */
  retenu: LibraryItem
}

/** Titre de présentation uniquement : l'identité et le Markdown restent intacts. */
export function displayMeetingTitle(title: string, source: string | null | undefined): string {
  const looksLikeSlug = title.includes('-') && !/\s/.test(title)
  if (!looksLikeSlug || !source) return title
  return source.replace(/\.[^.]+$/, '')
}

/**
 * Titre lisible d'un DOCUMENT, transcript ou compte-rendu. Un compte-rendu porte
 * « <slug> — compte-rendu » : sa base est rendue lisible, le suffixe gardé.
 * `source` est, pour un compte-rendu, celle du transcript dont il est tiré.
 */
export function displayDocumentTitle(title: string, source: string | null | undefined): string {
  const suffixe = title.match(/^(\S+)(\s+—\s+.+)$/)
  if (suffixe) return displayMeetingTitle(suffixe[1], source) + suffixe[2]
  return displayMeetingTitle(title, source)
}

/** Adapte le contrat serveur orienté réunion aux vues existantes. */
export function reunionDepuisApi(meeting: Meeting): Reunion {
  const retenu = meeting.preferred_transcript ?? meeting.transcripts[0] ?? meeting.reports[0]
  if (!retenu) throw new Error(`Réunion vide : ${meeting.id}`)
  return {
    cle: meeting.id,
    source: meeting.source,
    titre: meeting.title,
    date: meeting.date,
    duree: meeting.duration,
    langue: meeting.language,
    projet: meeting.project,
    nSpeakers: meeting.n_speakers,
    avertissements: meeting.quality_reservations.reduce((total, item) => total + item.count, 0),
    transcripts: meeting.transcripts,
    comptesRendus: meeting.reports,
    retenu,
  }
}

const estCompteRendu = (i: LibraryItem) => i.type === 'report'

/**
 * Version à présenter en premier : diarisé, puis profilé, puis plus récent.
 *
 * Volontairement plus simple que `core/dedup.py::_score()`, qui pondère aussi la
 * longueur du corps — parce qu'ici il ne s'agit pas de recommander une
 * suppression mais seulement de choisir ce qu'on montre d'abord. Une erreur ne
 * coûte rien : les autres versions sont à un clic.
 */
function rang(i: LibraryItem): number {
  return (i.diarization ? 4 : 0) + (i.profiled ? 2 : 0)
}

export function grouperParReunion(items: LibraryItem[]): Reunion[] {
  const parNom = new Map(items.map((i) => [i.name, i]))
  const parId = new Map(items.filter((i) => i.id).map((i) => [i.id, i]))

  /** Identité stable d'abord ; les noms ne sont que le repli legacy. */
  const cleDe = (i: LibraryItem): string => {
    if (i.meeting_id) return i.meeting_id
    if (estCompteRendu(i)) {
      const parent = (i.source_document_id ? parId.get(i.source_document_id) : undefined)
        ?? (i.source_transcript ? parNom.get(i.source_transcript) : undefined)
      // Parent absent — filtré par une recherche, ou supprimé. Le CR ne doit
      // PAS disparaître : il forme sa propre entrée plutôt que d'être avalé.
      if (parent) return parent.meeting_id || parent.source || parent.name
      return i.source_transcript || i.name
    }
    return i.source || i.name
  }

  const groupes = new Map<string, LibraryItem[]>()
  for (const i of items) {
    const k = cleDe(i)
    const g = groupes.get(k)
    if (g) g.push(i)
    else groupes.set(k, [i])
  }

  const reunions: Reunion[] = []
  for (const [cle, membres] of groupes) {
    const transcripts = membres.filter((i) => !estCompteRendu(i))
      .sort((a, b) => rang(b) - rang(a) || b.modified - a.modified)
    const comptesRendus = membres.filter(estCompteRendu)
      .sort((a, b) => b.modified - a.modified)

    // Un groupe sans transcript n'arrive que si le parent est hors de la liste
    // courante ; on retient alors le CR pour ne rien perdre à l'affichage.
    const retenu = transcripts[0] ?? comptesRendus[0]
    if (!retenu) continue

    reunions.push({
      cle,
      source: retenu.source || null,
      titre: retenu.title,
      date: membres.map((i) => i.date).filter(Boolean).sort()[0] || retenu.date,
      duree: retenu.duration,
      langue: retenu.language,
      projet: retenu.project,
      nSpeakers: retenu.n_speakers,
      avertissements: membres.reduce((n, i) => n + (i.warnings || 0), 0),
      transcripts,
      comptesRendus,
      retenu,
    })
  }
  return reunions
}

/**
 * Les réunions voisines d'une réunion, dans SON projet.
 *
 * « Précédente » est la plus ancienne qui la précède, « suivante » la plus récente qui la suit :
 * l'ordre est chronologique (date, puis clé pour départager), quel que soit le tri de la liste
 * affichée. Le projet est celui de la réunion, jamais le projet consulté : ouverte depuis « Tous
 * mes projets », une réunion garde pour voisines celles de son projet. Aux extrémités, la
 * voisine manquante est `undefined`.
 */
export function voisines(toutes: Reunion[], cle: string): { precedente?: Reunion; suivante?: Reunion } {
  const courante = toutes.find((r) => r.cle === cle)
  if (!courante) return {}
  const projet = courante.retenu.project_id
  const meme = toutes
    .filter((r) => r.retenu.project_id === projet)
    .sort((a, b) => a.date.localeCompare(b.date) || a.cle.localeCompare(b.cle))
  const i = meme.findIndex((r) => r.cle === cle)
  return { precedente: meme[i - 1], suivante: meme[i + 1] }
}
