import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReportResult, TranscriptDetail } from '../api'
import { audioUrl, downloadUrl, getConfig, getTranscript, makeReport } from '../api'
import { getLlmProvider } from '../lib/llmProvider'
import { Icon } from './Icon'
import { MarkdownView } from './MarkdownView'
import { ReportView } from './ReportView'
import { useLatestRequest } from '../lib/latestRequest'
import { decouperEnTours, locuteursDe, reperesDe } from '../lib/transcriptTurns'
import { SpeakerDot } from '../design/primitives'
import { useAudio } from '../audio/AudioProvider'
import { Waveform } from '../audio/Waveform'
import { useCloudUltra } from '../lib/cloudUltra'
import { useLang } from '../lib/langContext'
import { usePartage } from '../lib/partage'

type Props = { name: string
  /**
   * Portée d'ouverture du DOCUMENT, pas du projet courant — OBLIGATOIRE.
   *
   * Le lecteur s'ouvre depuis quatre endroits, dont deux (Recherche,
   * Assistant) peuvent élargir leur portée à `all` par un bouton dont l'état
   * ne remonte nulle part. Le seul fait fiable est celui que porte le résultat
   * ouvert : son `project_id`, ou `all` quand il est nul. Facultative, cette
   * prop serait oubliée par le prochain appelant, et l'oubli est muet.
   */
  documentScope: string
  /** Titre lisible pour le mini-lecteur « en cours d'écoute » (repli : le nom). */
  title?: string
  onBack: () => void; backLabel?: string
  /** La vue réunion porte déjà son propre retour : éviter d'en afficher deux. */
  hideBack?: boolean
  /** Retire la carte imbriquée quand le lecteur vit dans l'espace Réunion. */
  embedded?: boolean
  /** Passage demandé depuis une recherche ou une citation RAG. */
  initialTimestamp?: string | null
  /** Une citation : se placer ET lancer la lecture (un passage de recherche se place seulement). */
  lancer?: boolean
  /** Notifie la vue réunion afin qu'elle recharge son agrégat après création. */
  onReportCreated?: (name: string) => void
  /** Action d'archivage unitaire du document. */
  onArchive?: (name: string) => void
}

// Timestamp d'une ligne diarisée : « [00:12:34] » ou « [12:34] ».
const TS = /\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]/

function toSeconds(m: RegExpMatchArray): number {
  const a = Number(m[1]), b = Number(m[2]), c = m[3] ? Number(m[3]) : null
  return c === null ? a * 60 + b : a * 3600 + b * 60 + c
}

function timestampSeconds(value?: string | null): number | null {
  const match = value?.match(/(\d{1,2}):(\d{2})(?::(\d{2}))?/)
  return match ? toSeconds(match) : null
}

export function TranscriptReader({
  name, documentScope, title, onBack, backLabel, hideBack = false,
  embedded = false, initialTimestamp = null, lancer = false, onReportCreated, onArchive,
}: Props) {
  const { t } = useLang()
  // Un compte-rendu s'écrit dans le projet du propriétaire : jamais en lecture seule.
  const { lectureSeule } = usePartage()
  const [detail, setDetail] = useState<TranscriptDetail | null>(null)
  const [report, setReport] = useState<ReportResult | null>(null)
  const [reportTimestamp, setReportTimestamp] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)
  // Le rail des locuteurs et des repères est replié par défaut sur petit écran (voir `.rail-contenu` dans App.css).
  const [railOuvert, setRailOuvert] = useState(false)
  const [error, setError] = useState('')
  // L'audio est GLOBAL (mini-lecteur persistant) : le lecteur ne possède plus
  // son propre <audio>. Le passage courant se déduit de la position du lecteur
  // global, mais SEULEMENT quand c'est bien ce document qui est chargé.
  const {
    track: audioTrack, currentTime: audioTime, duration: audioDuration,
    play: audioPlay, seek: audioSeek,
  } = useAudio()
  const estCourant = audioTrack?.name === name
  const playhead = estCourant ? audioTime : -1
  // Durée fiable uniquement quand C'EST ce document qui est chargé : la frise
  // d'écoute ne place tête de lecture et repères qu'à partir de là.
  const dureeConnue = estCourant ? audioDuration : 0
  const monTrack = useMemo(
    () => ({ name, scope: documentScope, title: title ?? name }),
    [name, documentScope, title],
  )
  // `HTMLElement` et non `HTMLDivElement` : les deux rendus n'utilisent pas la
  // même balise — `<div>` dans la `<pre>` de repli, `<p>` dans les tours.
  const lineRefs = useRef<(HTMLElement | null)[]>([])
  const positionedPassage = useRef('')
  const [beginDetail, isCurrentDetail, invalidateDetail] = useLatestRequest()
  const [beginReport, isCurrentReport, invalidateReport] = useLatestRequest()

  useEffect(() => {
    // `generating` était OUBLIÉ ici : changer de document pendant une
    // génération laissait le bouton bloqué sur « Génération… » pour toujours.
    setDetail(null); setReport(null); setReportTimestamp(null)
    setError(''); setGenerating(false)
    invalidateReport()
    positionedPassage.current = ''
    // Sans ce catch explicite, une erreur laissait « Chargement… » à l'écran
    // indéfiniment, sans jamais dire ce qui n'allait pas.
    const request = beginDetail()
    getTranscript(name, documentScope)
      .then((value) => { if (isCurrentDetail(request)) setDetail(value) })
      .catch((e) => {
        if (isCurrentDetail(request)) setError(t('reader.openError', { error: String(e) }))
      })
    return () => invalidateDetail(request)
  }, [
    beginDetail, documentScope, invalidateDetail, invalidateReport, isCurrentDetail, name, t,
  ])

  const audioOk = Boolean(detail?.has_audio)
  // Transcrit sans horodatage (provenance) : un CR n'y pourrait rien citer.
  const sansHorodatage = (detail?.meta?.pipeline as { stt?: { timestamps?: boolean } } | undefined)
    ?.stt?.timestamps === false

  // Ce document devient l'objet d'écoute courant dès qu'il s'ouvre AVEC audio :
  // chargé et repositionné à la reprise, mais PAS lancé (ouvrir n'est pas
  // écouter). Le mini-lecteur global le reflète alors.
  useEffect(() => {
    if (audioOk && !estCourant) audioPlay(monTrack, { autoplay: false })
  }, [audioOk, estCourant, monTrack, audioPlay])

  // Un document ultra-confidentiel ne part vers aucun fournisseur cloud, sauf si le
  // compte l'autorise (panneau Confidentialité) : sinon son CR se génère avec le
  // fournisseur choisi s'il est local, ou avec le local par défaut.
  const cloudUltra = useCloudUltra()
  // La sensibilité EFFECTIVE, celle de la réunion : un CR d'une réunion
  // ultra-confidentielle l'est aussi, quel que soit son propre niveau stocké.
  const ultra = (detail?.sensitivity_effective ?? detail?.meta?.sensitivity) === 'ultra_confidential' && !cloudUltra

  async function fournisseurDuCR(): Promise<string | undefined> {
    if (!ultra) return undefined
    const config = await getConfig()
    const choisi = getLlmProvider(config.default_llm_provider || 'mlx')
    return (config.local_providers || ['mlx']).includes(choisi) ? choisi : (config.default_llm_provider || 'mlx')
  }

  async function generate() {
    const request = beginReport()
    setGenerating(true); setError('')
    try {
      const local = await fournisseurDuCR()
      const value = await (local ? makeReport(name, documentScope, local) : makeReport(name, documentScope))
      if (isCurrentReport(request)) {
        setReport(value)
        onReportCreated?.(value.name)
      }
    } catch (e) {
      if (isCurrentReport(request)) setError(String(e))
    } finally {
      if (isCurrentReport(request)) setGenerating(false)
    }
  }

  // Découpe le document en lignes, en repérant celles qui portent un timestamp.
  const lines = useMemo(() => {
    // L'API expose `body` sans front-matter. `content` reste un repli pour les
    // anciennes réponses, mais ne doit pas faire apparaître le YAML interne
    // dans le lecteur destiné à l'utilisateur.
    const raw = (detail?.body ?? detail?.content ?? '').split('\n')
    return raw.map((text) => {
      const m = text.match(TS)
      return { text, at: m ? toSeconds(m) : null, mark: m?.[0] ?? null }
    })
  }, [detail])

  // Le document se lit en tours de parole plutôt qu'en mur
  // monospace. `decouperEnTours` ne rend rien quand il n'y a pas d'horodatage
  // — un transcript en texte continu n'a aucune structure à montrer, et la
  // `<pre>` d'origine reste alors le rendu honnête.
  const { preambule, tours } = useMemo(
    () => decouperEnTours(detail?.body ?? detail?.content ?? ''), [detail])
  const enTours = tours.length > 0

  const locuteurs = useMemo(() => locuteursDe(tours), [tours])
  const reperes = useMemo(() => reperesDe(tours), [tours])

  // Repères de la frise d'écoute : les mêmes horodatages notables que le rail,
  // positionnés en fraction de la durée réelle. Aucun « chapitre » inventé — ce
  // sont les jalons présents dans le transcript.
  const chapitres = useMemo(() => {
    if (!(dureeConnue > 0)) return []
    return reperes
      .filter((r) => r.at <= dureeConnue)
      .map((r) => ({ ratio: r.at / dureeConnue, mark: r.mark }))
  }, [reperes, dureeConnue])
  const progression = dureeConnue > 0 && playhead >= 0 ? playhead / dureeConnue : 0
  // Couleur STABLE par locuteur : dérivée de l'ordre d'apparition dans le rail,
  // partagée entre le rail et les en-têtes de tour, plutôt qu'un rendu
  // monochrome.
  const teinteLocuteur = useMemo(() => {
    const map = new Map<string, number>()
    locuteurs.forEach((locuteur, index) => map.set(locuteur.nom, index))
    return map
  }, [locuteurs])

  /** Index du premier passage de chaque tour, dans l'ordre du DOM. */
  const debuts = useMemo(() => {
    const out: number[] = []
    let n = 0
    for (const tour of tours) { out.push(n); n += tour.passages.length }
    return out
  }, [tours])

  // Les instants de chaque bloc rendu, DANS L'ORDRE DU DOM — un seul tableau
  // pour les deux rendus. La ligne active et le défilement vers un passage
  // cité se calculent alors pareil, qu'on lise des tours ou une `<pre>`.
  const instants = useMemo(
    () => (enTours
      ? tours.flatMap((tour) => tour.passages.map((passage) => passage.at))
      : lines.map((ligne) => ligne.at)),
    [enTours, lines, tours],
  )

  // Bloc en cours de lecture = dernier bloc horodaté avant le playhead.
  const activeIndex = useMemo(() => {
    if (playhead < 0) return -1
    let idx = -1
    for (let i = 0; i < instants.length; i++) {
      const at = instants[i]
      if (at !== null && at <= playhead) idx = i
    }
    return idx
  }, [instants, playhead])

  const requestedSecond = useMemo(
    () => timestampSeconds(reportTimestamp ?? initialTimestamp),
    [initialTimestamp, reportTimestamp],
  )

  useEffect(() => {
    if (!detail || requestedSecond === null) return
    // Quand il y a de l'audio, on attend que CE document soit chargé dans le
    // lecteur global — sinon `seek` n'aurait pas d'élément où se placer.
    if (audioOk && !estCourant) return
    const passageKey = `${name}:${requestedSecond}`
    if (positionedPassage.current === passageKey) return
    positionedPassage.current = passageKey
    // Une citation (vue réunion, Assistant, CR lu ici) demande à ENTENDRE le passage.
    audioSeek(requestedSecond, lancer || reportTimestamp !== null ? { autoplay: true } : undefined)
    let target = -1
    for (let i = 0; i < instants.length; i++) {
      const at = instants[i]
      if (at !== null && at <= requestedSecond) target = i
    }
    if (target >= 0) {
      lineRefs.current[target]?.scrollIntoView?.({ block: 'center' })
    }
  }, [detail, audioOk, estCourant, instants, name, requestedSecond, audioSeek, lancer, reportTimestamp])

  function seek(seconds: number) {
    // Un horodatage DANS le texte : l'utilisateur demande à ENTENDRE ce passage.
    audioPlay(monTrack, { startAt: seconds, autoplay: true })
  }

  /** Défilement sur la frise : se placer sans lancer (comme un repère du rail). */
  function scrub(ratio: number) {
    const seconds = ratio * (dureeConnue || 0)
    if (estCourant) audioSeek(seconds)
    else audioPlay(monTrack, { startAt: seconds, autoplay: false })
  }

  /**
   * Un repère du rail : on se déplace, on ne joue pas.
   *
   * Différence délibérée avec `seek`, déclenché par un horodatage DANS le
   * texte : là, l'utilisateur demande à entendre ce passage. Ici il demande à
   * le voir. Démarrer le son sur un clic de navigation ferait sursauter un
   * bureau ouvert.
   */
  function allerA(index: number, seconds: number) {
    // Un repère du rail : se DÉPLACER, pas écouter.
    if (estCourant) audioSeek(seconds)
    else audioPlay(monTrack, { startAt: seconds, autoplay: false })
    lineRefs.current[index]?.scrollIntoView?.({ block: 'center' })
  }

  /** Le nom d'usage d'un locuteur, quand une empreinte vocale l'a identifié. */
  function nomAffiche(locuteur: string): string {
    const voix = detail?.voices?.[locuteur]
    if (!voix) return locuteur
    if (voix.label) return voix.label
    // Une voix anonyme : le libellé de la langue de l'interface, au numéro du
    // « Speaker N » de ce transcript. Le numéro du registre est global : lu ici, il
    // ne correspondait à aucun locuteur du texte.
    const numero = /(\d+)\s*$/.exec(locuteur)?.[1]
    return t('voices.name', { id: numero ?? voix.voice_id })
  }

  // Le front-matter fait foi : `type: report` est écrit par le générateur.
  // Se fier au seul nom cassait dès qu'il y avait collision — un CR régénéré
  // s'appelle « …-cr-1.md » et redevenait éligible au bouton « Compte-rendu »,
  // donnant un compte-rendu de compte-rendu. Le nom ne sert plus que de repli
  // tant que le détail n'est pas chargé.
  const isReport = detail
    ? detail.meta?.type === 'report'
    : /-cr(-\d+)?\.md$/i.test(name)

  if (report) {
    return (
      <section className={`card transcript-reader ${embedded ? 'embedded' : ''}`}>
        <div className="row">
          <button className="btn" onClick={() => setReport(null)}>{t('reader.backTranscript')}</button>
          <strong>{report.name}</strong>
          {/* Le compte-rendu naît dans le projet de son transcript : même portée. */}
          <a className="btn" href={downloadUrl(report.name, documentScope)}>{t('common.download')}</a>
        </div>
        <p className="muted small">{t('reader.savedToLibrary')}</p>
        <ReportView
          content={report.content}
          genre={report.genre}
          onTimestamp={(timestamp) => {
            setReportTimestamp(timestamp)
            setReport(null)
          }}
        />
      </section>
    )
  }

  return (
    <section className={`card transcript-reader ${embedded ? 'embedded' : ''}`}>
      <div className="row document-toolbar">
        {!hideBack && <button className="btn" onClick={onBack}>{backLabel ?? t('reader.back')}</button>}
        <span className="document-label"><span className="action-scope">{t('common.activeDocument')}</span><strong>{name}</strong></span>
        {!isReport && !lectureSeule && (
          <button className="btn accent llm" onClick={generate} disabled={generating || !detail || sansHorodatage}
                  title={sansHorodatage ? t('reader.generate.needsTimestamps')
                    : ultra ? t('reader.generate.localOnly') : undefined}>
            {!generating && <Icon name="report" />}{generating ? t('reader.generating') : t('reader.generate')}
          </button>
        )}
        <a className="btn" href={downloadUrl(name, documentScope)}>{t('common.download')}</a>
        {onArchive && (
          <button type="button" className="btn danger-subtle" onClick={() => onArchive(name)}>
            <Icon name="archive" /> {t('common.archive')}
          </button>
        )}
      </div>

      {error && <p className="error"><Icon name="error" /> {error}</p>}
      {generating && (
        <p className="muted small">
          {t('reader.generatingHint')}
        </p>
      )}

      {detail?.language_unstable && (
        <p className="warn small">
          <Icon name="alert" /> {t('reader.mixedLang.pre')}
          <strong>{Math.round((detail.language_mix ?? 0) * 100)}&nbsp;%</strong>
          {t('reader.mixedLang.mid')}<strong>{t('reader.mixedLang.strong')}</strong>
          {t('reader.mixedLang.post')}
        </p>
      )}

      {/* La correspondance voix↔locuteur vit dans le rail quand il existe.
          Ici, elle n'a nulle part où aller : sans horodatage, pas de rail. */}
      {!enTours && detail && detail.voices && Object.keys(detail.voices).length > 0 && (
        <div className="voice-legend">
          <span className="muted small"><Icon name="volume" /> {t('reader.voicesLabel')}</span>
          {Object.keys(detail.voices).map((spk) => (
            <span key={spk} className="voice-chip" title={t('reader.voiceRecognized')}>
              {spk} → <strong>{nomAffiche(spk)}</strong>
            </span>
          ))}
        </div>
      )}

      {audioOk && (
        <>
          <Waveform
            url={audioUrl(name, documentScope)}
            actif={audioOk}
            progress={progression}
            seconds={estCourant ? Math.max(0, audioTime) : 0}
            duration={dureeConnue}
            chapters={chapitres}
            onScrub={scrub}
          />
          <p className="muted small player-hint">
            <Icon name="volume" /> {t('reader.playerHint')}
          </p>
        </>
      )}

      {detail && enTours ? (
        <div className="transcript-lecture">
          <aside className="transcript-rail" aria-label={t('reader.rail.aria')}>
            {/* Petit écran : le rail est replié, le texte vient d'abord ; ce bouton le déplie. Au bureau il est caché. */}
            <button type="button" className="rail-bascule" aria-expanded={railOuvert} aria-controls="rail-contenu"
                    onClick={() => setRailOuvert((o) => !o)}>
              {t('reader.rail.toggle')}
            </button>
            <div id="rail-contenu" className={`rail-contenu ${railOuvert ? 'ouvert' : ''}`}>
            {locuteurs.length > 0 && (
              <div className="rail-bloc">
                <h3>{t('reader.rail.speakers')}</h3>
                <ul>
                  {locuteurs.map((locuteur) => {
                    const premier = tours.findIndex((tour) => tour.locuteur === locuteur.nom)
                    const affiche = nomAffiche(locuteur.nom)
                    return (
                      <li key={locuteur.nom}>
                        <button
                          type="button"
                          onClick={() => allerA(debuts[premier], tours[premier].passages[0].at)}
                          title={t('reader.rail.gotoFirst', { nom: affiche })}
                        >
                          <span className="rail-who">
                            <SpeakerDot index={teinteLocuteur.get(locuteur.nom) ?? 0} label={affiche} size={20} />
                            <span className="rail-nom">
                              {affiche}
                              {affiche !== locuteur.nom && <small>{locuteur.nom}</small>}
                            </span>
                          </span>
                          <span className="rail-compte">{locuteur.passages}</span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}
            {reperes.length > 1 && (
              <div className="rail-bloc">
                <h3>{t('reader.rail.markers')}</h3>
                <ul className="rail-reperes">
                  {reperes.map((repere) => {
                    const index = instants.indexOf(repere.at)
                    return (
                      <li key={repere.at}>
                        <button type="button" onClick={() => allerA(index, repere.at)}>
                          {repere.mark}
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}
            </div>
          </aside>

          <div className="transcript-texte">
            {preambule && (
              <details className="transcript-preambule">
                <summary>{t('reader.preamble')}</summary>
                <MarkdownView content={preambule} onTimestamp={setReportTimestamp} />
              </details>
            )}
            {tours.map((tour, ti) => (
              <article key={debuts[ti]} className="tour">
                {tour.locuteur && (
                  <h3 className="tour-locuteur" dir="auto">
                    <SpeakerDot index={teinteLocuteur.get(tour.locuteur) ?? 0} label={nomAffiche(tour.locuteur)} size={22} />
                    <span className="tour-who">
                      {nomAffiche(tour.locuteur)}
                      {nomAffiche(tour.locuteur) !== tour.locuteur && (
                        <small title={t('reader.voiceRecognized')}>{tour.locuteur}</small>
                      )}
                    </span>
                  </h3>
                )}
                <div className="tour-passages">
                  {tour.passages.map((passage, pi) => {
                    const index = debuts[ti] + pi
                    return (
                      // `dir="auto"` par TOUR, et non sur le document : un
                      // plateau à interprètes alterne les directions, et une
                      // direction globale en coucherait la moitié à l'envers.
                      // Le navigateur tranche sur le premier caractère FORT,
                      // donc l'horodatage neutre qui précède ne le trompe pas.
                      <p
                        key={index}
                        ref={(node) => { lineRefs.current[index] = node }}
                        className={index === activeIndex ? 'tline on' : 'tline'}
                        dir="auto"
                      >
                        <button
                          className="ts"
                          onClick={() => seek(passage.at)}
                          disabled={!audioOk}
                          title={audioOk ? t('reader.listenFromHere') : t('reader.noAudio')}
                        >
                          {passage.mark}
                        </button>
                        {passage.texte}
                      </p>
                    )
                  })}
                </div>
              </article>
            ))}
          </div>
        </div>
      ) : detail ? (
        <pre className="preview tall">
          {lines.map((l, i) => {
            const active = i === activeIndex
            if (l.at === null || !l.mark) {
              return <div key={i} ref={(node) => { lineRefs.current[i] = node }} className={active ? 'tline on' : 'tline'}>{l.text || ' '}</div>
            }
            const [before, after] = l.text.split(l.mark)
            return (
              <div key={i} ref={(node) => { lineRefs.current[i] = node }} className={active ? 'tline on' : 'tline'} dir="auto">
                {before}
                <button
                  className="ts"
                  onClick={() => seek(l.at!)}
                  disabled={!audioOk}
                  title={audioOk ? t('reader.listenFromHere') : t('reader.noAudio')}
                >
                  {l.mark}
                </button>
                {after}
              </div>
            )
          })}
        </pre>
      ) : error ? null : (
        <p className="muted">{t('reader.loading')}</p>
      )}
    </section>
  )
}
