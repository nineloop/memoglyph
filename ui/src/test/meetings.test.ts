import { describe, expect, it } from 'vitest'
import type { LibraryItem } from '../api'
import { displayDocumentTitle, grouperParReunion, voisines } from '../lib/meetings'

/**
 * Le regroupement est la nouvelle porte d'entrée du corpus : s'il perd un
 * document, l'utilisateur n'a AUCUN autre moyen de le retrouver depuis cet
 * écran. D'où l'insistance sur les cas dégradés.
 */

let n = 0
function doc(p: Partial<LibraryItem> = {}): LibraryItem {
  n += 1
  return {
    name: `doc-${n}.md`, title: `Doc ${n}`, date: '2026-07-20',
    duration: '00:10:00', language: 'fr', engine: 'whisper', source: 'media.mp4',
    project: 'default', diarization: false, profiled: false, n_speakers: null,
    id: `document-${n}`, meeting_id: null, project_id: null,
    type: null, source_transcript: null, source_document_id: null,
    warnings: 0, size: 100, modified: n,
    ...p,
  } as LibraryItem
}

describe('grouperParReunion', () => {
  it('utilise meeting_id avant le nom du média', () => {
    const r = grouperParReunion([
      doc({ name: 'a.md', meeting_id: 'meeting-a', source: 'same.mp4' }),
      doc({ name: 'b.md', meeting_id: 'meeting-b', source: 'same.mp4' }),
      doc({ name: 'c.md', meeting_id: 'meeting-a', source: 'other.mp4' }),
    ])
    expect(r).toHaveLength(2)
    expect(r.find((meeting) => meeting.cle === 'meeting-a')?.transcripts).toHaveLength(2)
    expect(r.find((meeting) => meeting.cle === 'meeting-a')?.source).not.toBe('meeting-a')
  })

  it('un CR reste rattaché après le renommage de son transcript', () => {
    const r = grouperParReunion([
      doc({ id: 'source-id', name: 'nouveau.md', meeting_id: 'meeting' }),
      doc({
        name: 'report.md', type: 'report', meeting_id: 'meeting',
        source_document_id: 'source-id', source_transcript: 'ancien.md', source: null,
      }),
    ])
    expect(r).toHaveLength(1)
    expect(r[0].comptesRendus).toHaveLength(1)
  })

  it('regroupe les versions d’un même média sur une seule réunion', () => {
    const r = grouperParReunion([
      doc({ name: 'a.md', source: 'entretien.mp4' }),
      doc({ name: 'a-1.md', source: 'entretien.mp4' }),
      doc({ name: 'a-2.md', source: 'entretien.mp4' }),
    ])
    expect(r).toHaveLength(1)
    expect(r[0].transcripts).toHaveLength(3)
  })

  it('rattache un compte-rendu au média de SON transcript', () => {
    const r = grouperParReunion([
      doc({ name: 'a.md', source: 'entretien.mp4' }),
      doc({ name: 'a-cr.md', type: 'report', source_transcript: 'a.md', source: null }),
    ])
    expect(r).toHaveLength(1)
    expect(r[0].comptesRendus).toHaveLength(1)
  })

  it('sépare deux médias différents', () => {
    const r = grouperParReunion([
      doc({ source: 'un.mp4' }), doc({ source: 'deux.mp4' }),
    ])
    expect(r).toHaveLength(2)
  })

  // --- Cas dégradés : rien ne doit disparaître --------------------------------

  it('ne PERD PAS un compte-rendu dont le parent est absent de la liste', () => {
    // Arrive dès qu'une recherche filtre le transcript mais pas son CR.
    const r = grouperParReunion([
      doc({ name: 'orphelin-cr.md', type: 'report', source_transcript: 'absent.md', source: null }),
    ])
    expect(r).toHaveLength(1)
    expect(r[0].retenu.name).toBe('orphelin-cr.md')
  })

  it('retombe sur le nom du fichier quand `source` est vide', () => {
    const r = grouperParReunion([doc({ name: 'sans-source.md', source: null })])
    expect(r).toHaveLength(1)
    expect(r[0].cle).toBe('sans-source.md')
  })

  it('ne perd aucun document, quelle que soit l’entrée', () => {
    const entree = [
      doc({ name: 'a.md', source: 'x.mp4' }),
      doc({ name: 'a-cr.md', type: 'report', source_transcript: 'a.md', source: null }),
      doc({ name: 'b.md', source: null }),
      doc({ name: 'c-cr.md', type: 'report', source_transcript: 'nulle-part.md', source: null }),
    ]
    const total = grouperParReunion(entree)
      .reduce((n, r) => n + r.transcripts.length + r.comptesRendus.length, 0)
    expect(total).toBe(entree.length)
  })

  it('rend une liste vide sur une entrée vide', () => {
    expect(grouperParReunion([])).toEqual([])
  })

  // --- Version retenue -------------------------------------------------------

  it('retient la version diarisée ET profilée', () => {
    const r = grouperParReunion([
      doc({ name: 'brut.md', source: 'x.mp4', modified: 99 }),
      doc({ name: 'riche.md', source: 'x.mp4', diarization: true, profiled: true, modified: 1 }),
    ])
    expect(r[0].retenu.name).toBe('riche.md')
  })

  it('départage deux versions équivalentes par la plus récente', () => {
    const r = grouperParReunion([
      doc({ name: 'vieux.md', source: 'x.mp4', diarization: true, modified: 1 }),
      doc({ name: 'neuf.md', source: 'x.mp4', diarization: true, modified: 9 }),
    ])
    expect(r[0].retenu.name).toBe('neuf.md')
  })

  // --- Agrégats de la ligne --------------------------------------------------

  it('additionne les réserves de TOUS les éléments, CR compris', () => {
    const r = grouperParReunion([
      doc({ name: 'a.md', source: 'x.mp4', warnings: 2 }),
      doc({ name: 'a-cr.md', type: 'report', source_transcript: 'a.md', source: null, warnings: 3 }),
    ])
    expect(r[0].avertissements).toBe(5)
  })

  it('affiche la date la plus ANCIENNE — celle de la réunion', () => {
    const r = grouperParReunion([
      doc({ name: 'a.md', source: 'x.mp4', date: '2026-07-19' }),
      doc({ name: 'a-1.md', source: 'x.mp4', date: '2026-07-21', diarization: true }),
    ])
    expect(r[0].date).toBe('2026-07-19')
  })
})

describe('displayDocumentTitle', () => {
  const source = 'AMI IS1009a, réunion de lancement d’une télécommande.wav'

  it('rend le nom de la source à un transcript resté en slug, sans extension', () => {
    expect(displayDocumentTitle('ami-is1009a-réunion-de-lancement-dune-télécommande', source))
      .toBe('AMI IS1009a, réunion de lancement d’une télécommande')
  })

  it('rend lisible la base d’un compte-rendu et garde son suffixe', () => {
    expect(displayDocumentTitle('ami-is1009a-réunion-de-lancement-dune-télécommande — compte-rendu', source))
      .toBe('AMI IS1009a, réunion de lancement d’une télécommande — compte-rendu')
  })

  it('garde un titre saisi, et un slug sans source connue', () => {
    expect(displayDocumentTitle('Comité de pilotage — compte-rendu', source)).toBe('Comité de pilotage — compte-rendu')
    expect(displayDocumentTitle('ami-is1009a', null)).toBe('ami-is1009a')
  })
})

describe('voisines (retour 15 : réunion précédente et suivante)', () => {
  const reunion = (cle: string, date: string, projet: string | null) =>
    grouperParReunion([doc({ name: `${cle}.md`, meeting_id: cle, date, project_id: projet })])[0]

  const a = reunion('a', '2026-10-01', 'P1')
  const b = reunion('b', '2026-10-03', 'P1')
  const c = reunion('c', '2026-10-05', 'P1')
  const autre = reunion('z', '2026-10-04', 'P2')

  it('donne la plus ancienne qui précède et la plus récente qui suit, dans le projet de la réunion', () => {
    const v = voisines([c, a, autre, b], 'b')
    expect(v.precedente?.cle).toBe('a')
    expect(v.suivante?.cle).toBe('c')
  })

  it('ne tient pas compte du tri de la liste affichée', () => {
    expect(voisines([c, b, a], 'a').suivante?.cle).toBe('b')
    expect(voisines([a, b, c], 'c').precedente?.cle).toBe('b')
  })

  it('ignore les réunions des autres projets, même quand la liste est « Tous mes projets »', () => {
    const v = voisines([a, autre, b, c], 'c')
    expect(v.suivante).toBeUndefined()
    expect(v.precedente?.cle).toBe('b')
  })

  it('absentes aux extrémités ; un seul élément ; réunion inconnue', () => {
    expect(voisines([a, b, c], 'a').precedente).toBeUndefined()
    expect(voisines([a, b, c], 'c').suivante).toBeUndefined()
    expect(voisines([a], 'a')).toEqual({ precedente: undefined, suivante: undefined })
    expect(voisines([a], 'inconnue')).toEqual({})
  })

  it('départage deux réunions de même date par leur clé (ordre stable)', () => {
    const x = reunion('x', '2026-10-02', 'P1')
    const y = reunion('y', '2026-10-02', 'P1')
    expect(voisines([y, x], 'x').suivante?.cle).toBe('y')
    expect(voisines([x, y], 'y').precedente?.cle).toBe('x')
  })
})
