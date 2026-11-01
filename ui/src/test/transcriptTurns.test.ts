import { describe, expect, it } from 'vitest'
import { decouperEnTours, locuteursDe, reperesDe } from '../lib/transcriptTurns'

/**
 * Le découpage en tours de parole, testé SANS React.
 *
 * Le format d'entrée n'est pas une convention d'interface : il est écrit par
 * `core/writer_md.py:render_markdown`. Les corps ci-dessous en reproduisent
 * les trois formes réelles.
 */

const DIARISE = [
  '# en-7locuteurs',
  '',
  '**Speaker 1** [00:00:01] If it is not a warning, then it is going to be an error.',
  '',
  '**Speaker 1** [00:00:10] If it is a warning, it may or may not be fatal.',
  '',
  '**Speaker 2** [00:00:16] And it is a decision.',
  '',
  '**Speaker 1** [00:00:18] Now, you may get an unexpected result.',
  '',
].join('\n')

describe('decouperEnTours', () => {
  it('regroupe les passages CONSÉCUTIFS d’un même locuteur', () => {
    const { tours } = decouperEnTours(DIARISE)
    expect(tours.map((t) => [t.locuteur, t.passages.length]))
      .toEqual([['Speaker 1', 2], ['Speaker 2', 1], ['Speaker 1', 1]])
    expect(tours[0].passages[1].texte).toBe('If it is a warning, it may or may not be fatal.')
    expect(tours[0].passages[1].mark).toBe('[00:00:10]')
    expect(tours[0].passages[1].at).toBe(10)
  })

  it('met tout ce qui précède le premier tour dans le préambule', () => {
    expect(decouperEnTours(DIARISE).preambule).toBe('# en-7locuteurs')
  })

  it('lit les deux formes d’horodatage', () => {
    const { tours } = decouperEnTours('[01:15] court\n\n**Bob** [01:02:03] long')
    expect(tours[0].passages[0].at).toBe(75)
    expect(tours[1].passages[0].at).toBe(3723)
  })

  it('REFUSE de voir un tour dans le profil LLM', () => {
    // `_render_profile_sections` écrit des lignes en gras qui ressemblent à des
    // prises de parole, et surtout du texte LIBRE — résumé, points clés — dont
    // le contenu n'est borné par rien. Une heure citée en clair y suffit.
    //
    // Sans l'exigence de crochets, « 12:34 marque le tournant » deviendrait le
    // premier passage : le préambule s'arrêterait là et TOUT le profil, y
    // compris le titre des sections, serait découpé comme une conversation.
    const profil = [
      '# réunion',
      '',
      '## Conversation',
      '',
      '**Type :** réunion de suivi',
      '*00:42:00 · 4 locuteurs · 118 tours*',
      '',
      '12:34 marque le tournant de la séance.',
      '',
      '## Locuteurs',
      '',
      '**Speaker 1 — Animateur** · 12:34 · 45 % · 51 tours',
      '**Speaker 2** 00:04:10 relance sur le budget.',
      '- *Ton :* direct',
      '- Ouvre la séance',
      '',
      '## Transcript',
      '',
      '**Speaker 1** [00:00:01] Bonjour à tous.',
      '',
    ].join('\n')

    const { preambule, tours } = decouperEnTours(profil)
    expect(tours).toHaveLength(1)
    expect(tours[0].locuteur).toBe('Speaker 1')
    expect(tours[0].passages).toHaveLength(1)
    // Chacune des trois lignes piégeuses reste DANS le préambule.
    expect(preambule).toContain('**Type :** réunion de suivi')
    expect(preambule).toContain('12:34 marque le tournant')
    expect(preambule).toContain('**Speaker 2** 00:04:10 relance')
    expect(preambule).toContain('## Transcript')
  })

  it('rend un corps SANS horodatage entièrement en préambule', () => {
    // Le troisième format de `writer_md` : un paragraphe continu. Rien à
    // découper — et surtout rien à perdre.
    const brut = 'Bonjour à tous. Nous commençons. La séance est ouverte.'
    expect(decouperEnTours(brut)).toEqual({ preambule: brut, tours: [] })
  })

  it('rattache une ligne non analysable au passage précédent', () => {
    const { tours } = decouperEnTours(
      '**Bob** [00:01] premier jet\nsuite ajoutée à la main\n\n**Bob** [00:09] autre')
    expect(tours[0].passages[0].texte).toBe('premier jet\nsuite ajoutée à la main')
    expect(tours[0].passages[1].texte).toBe('autre')
  })

  it('NE PERD aucune ligne du document', () => {
    // Ancre de non-vacuité et invariant à la fois : les tests ci-dessus
    // vérifient ce qui est produit, celui-ci vérifie qu'il n'est rien resté
    // dehors. Un parseur qui jetterait les lignes inattendues passerait tous
    // les autres.
    const source = [DIARISE, '', 'une ligne orpheline', '', '[00:01:00] fin'].join('\n')
    const { preambule, tours } = decouperEnTours(source)

    const vu = [
      preambule,
      ...tours.flatMap((t) => t.passages.flatMap((p) => [t.locuteur ?? '', p.mark, p.texte])),
    ].join('\n')

    const attendues = source.split('\n').map((l) => l.trim()).filter(Boolean)
    // Titre + quatre tours + une ligne orpheline + un tour nu. Compté, pas
    // estimé : sans ce chiffre exact, la boucle ci-dessous passerait aussi
    // bien sur zéro ligne.
    expect(attendues).toHaveLength(7)
    for (const ligne of attendues) {
      for (const fragment of ligne.replace(/\*\*/g, '').split(/\s*\[|\]\s*/).filter(Boolean)) {
        expect(vu).toContain(fragment.trim())
      }
    }
  })
})

describe('locuteursDe', () => {
  it('compte les passages par locuteur, dans l’ordre d’apparition', () => {
    expect(locuteursDe(decouperEnTours(DIARISE).tours))
      .toEqual([{ nom: 'Speaker 1', passages: 3 }, { nom: 'Speaker 2', passages: 1 }])
  })

  it('rend une liste VIDE sur un transcript non diarisé', () => {
    expect(locuteursDe(decouperEnTours('[00:10] seul').tours)).toEqual([])
  })
})

describe('reperesDe', () => {
  it('pose un repère par tranche, sur un passage qui existe', () => {
    const corps = Array.from({ length: 40 }, (_, i) =>
      `**A** [00:${String(i).padStart(2, '0')}:00] segment ${i}`).join('\n\n')
    const reperes = reperesDe(decouperEnTours(corps).tours, 600)
    expect(reperes.map((r) => r.mark))
      .toEqual(['[00:00:00]', '[00:10:00]', '[00:20:00]', '[00:30:00]'])
  })

  it('REPART du passage retenu après un long silence, pas de l’horloge', () => {
    // Le piège : avancer le seuil de `pas` en `pas` depuis zéro. Après une
    // heure sans parole, le seuil est resté à 5 minutes — largement dépassé —
    // et CHAQUE passage suivant décroche son propre repère. Une réunion avec
    // un long blanc produirait alors un repère par phrase.
    const corps = [
      '**A** [00:00:00] début',
      '**A** [01:00:00] après une heure',
      '**A** [01:01:00] une minute plus tard',
      '**A** [01:02:00] encore une',
    ].join('\n\n')
    expect(reperesDe(decouperEnTours(corps).tours, 300).map((r) => r.at))
      .toEqual([0, 3600])
  })

  it('rend une liste vide sans tours', () => {
    expect(reperesDe([])).toEqual([])
  })
})

/**
 * Les formats importés, lus comme les lit le compte rendu. Mêmes cas que
 * `tests/unit/test_formats_transcript.py` : la règle est écrite deux fois, ces
 * deux fichiers la tiennent alignée.
 */
const TRANSCRIPT = '# Essai\n\n## Transcript\n\n'
const lu = (...lignes: string[]) => decouperEnTours(TRANSCRIPT + lignes.join('\n')).tours
  .flatMap((t) => t.passages.map((p) => [p.mark, t.locuteur, p.texte]))

describe('formats importés', () => {
  it('affiche l’horodatage court sous la forme du compte rendu', () => {
    expect(lu('[00:05] Bonjour.', '[75:10] Plus tard.')).toEqual([
      ['[00:00:05]', null, 'Bonjour.'], ['[01:15:10]', null, 'Plus tard.']])
    expect(decouperEnTours('[75:10] Plus tard.').tours[0].passages[0].at).toBe(4510)
  })

  it('lit le locuteur hors gras avant l’horodatage, quatre mots sans chiffre', () => {
    expect(lu('Bruno Martin [00:01:05] D’accord.', 'Jean-Marie de La Tour [01:05] Quatre mots.')).toEqual([
      ['[00:01:05]', 'Bruno Martin', 'D’accord.'], ['[00:01:05]', 'Jean-Marie de La Tour', 'Quatre mots.']])
    expect(lu('Jean Pierre Marie Paul Louis [00:01:02] Cinq mots.')).toEqual([])
    expect(lu('Salle 4 [00:01:02] Un chiffre.')).toEqual([])
    expect(lu('Alice [00:01:02] Témoin.')).toHaveLength(1)
  })

  it('fait de « Nom : » un locuteur seulement s’il ouvre deux répliques', () => {
    expect(lu('[00:01:02] Budget : 25 €', '[00:01:05] Alice : On valide.', '[01:09] Alice: Suivant.')).toEqual([
      ['[00:01:02]', null, 'Budget : 25 €'],
      ['[00:01:05]', 'Alice', 'On valide.'], ['[00:01:09]', 'Alice', 'Suivant.']])
    expect(lu('[00:01:02] Budget : 25 €')).toEqual([['[00:01:02]', null, 'Budget : 25 €']])
    expect(lu('[00:00:01] Point 3 : a', '[00:00:02] Point 3 : b')).toEqual([
      ['[00:00:01]', null, 'Point 3 : a'], ['[00:00:02]', null, 'Point 3 : b']])
  })

  it('ne lit les formes importées ni dans le profil ni sous un locuteur en gras', () => {
    const corps = ['# Essai', '', 'Alice [00:00:06] dans le profil', '', '## Transcript', '',
      '**Alice** [00:01:02] Budget : 25 €', '**Bruno** [00:01:05] Budget : 30 €'].join('\n')
    const { preambule, tours } = decouperEnTours(corps)
    expect(preambule).toContain('Alice [00:00:06] dans le profil')
    expect(tours.map((t) => [t.locuteur, t.passages[0].texte])).toEqual([
      ['Alice', 'Budget : 25 €'], ['Bruno', 'Budget : 30 €']])
  })
})
