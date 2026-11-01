import { useCallback, useEffect, useMemo, useState } from 'react'
import type { LibraryItem } from '../api'
import { archiveTranscript, getArchivePlan, getConfig, getTranscript, porteeDocument, updateSensitivity } from '../api'
import { displayMeetingTitle, type Reunion } from '../lib/meetings'
import { reservesDe, type Reserve } from '../lib/reserves'
import { Icon } from './Icon'
import { ReportView } from './ReportView'
import { TranscriptReader } from './TranscriptReader'
import { FinopsSection } from './FinopsSection'
import { useLatestRequest } from '../lib/latestRequest'
import { Badge } from '../design/primitives'
import { useLang } from '../lib/langContext'
import { pluralKey } from '../lib/i18n'
import type { Traduire } from '../lib/i18n'
import { sensitivityLabelKey } from '../lib/messages'
import { usePartage } from '../lib/partage'
import { PartageDialog } from './PartageDialog'
import { EclFaits } from './EclFaits'
import { ONGLETS_REUNION, type OngletReunion } from '../lib/urlState'

type Props = {
  reunion: Reunion
  onBack: () => void
  /** Onglet imposé par l'URL, et son écho vers le parent. */
  initialTab?: string | null
  onTabChange?: (onglet: string) => void
  onVoirDoublons?: () => void
  onChanged?: () => void
}
const ONGLETS = ONGLETS_REUNION
type MeetingTab = OngletReunion

// Vocabulaire fermé de `core/sensitivity`. « ultra_confidential » garde tout en local
// (aucun fournisseur cloud, `sensitivity.cloud_refuse`). Les libellés vivent dans le
// catalogue via `sensitivityLabelKey` ; ici, seules les valeurs.
const SENS_VALEURS = ['ultra_confidential', 'confidential', 'standard'] as const
const normaliserSensibilite = (valeur: unknown): string =>
  SENS_VALEURS.includes(valeur as (typeof SENS_VALEURS)[number]) ? (valeur as string) : 'standard'

// Provenance d'un enregistrement du compagnon de capture, écrite par le serveur
// (`capture` du front-matter) ; absente d'une réunion importée à la main.
interface Capture { application?: string; mode?: 'visio' | 'replay' }

function lireCapture(valeur: unknown): Capture | null {
  if (!valeur || typeof valeur !== 'object') return null
  const { application, mode } = valeur as Record<string, unknown>
  const capture: Capture = {
    application: typeof application === 'string' && application ? application : undefined,
    mode: mode === 'visio' || mode === 'replay' ? mode : undefined,
  }
  return capture.application || capture.mode ? capture : null
}

function formatDay(value: string, t: Traduire): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
  return match ? `${match[3]}/${match[2]}/${match[1]}` : value || t('common.unknownDate')
}

function formatVersion(item: LibraryItem, t: Traduire): string {
  const modified = Number(item.modified)
  if (!Number.isFinite(modified) || modified <= 0) {
    return t('meeting.version.timeUnknown', { date: formatDay(item.date, t) })
  }
  return new Intl.DateTimeFormat('fr-FR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(modified * 1000))
}

function documentLabel(item: LibraryItem, t: Traduire): string {
  const kind = item.type === 'report' ? t('common.docKind.report') : t('common.docKind.transcript')
  return `${kind} · ${formatVersion(item, t)} · ${item.name}`
}

/**
 * Les cinq faits de la réunion, en UN seul endroit.
 *
 * L'en-tête plein les rend en colonnes libellées, l'en-tête compact en une
 * ligne de texte. Deux listes séparées auraient dérivé au premier ajout — et
 * `seul` existe parce qu'un fait sorti de sa colonne perd son libellé : « 3 »
 * ne dit rien, « 3 locuteurs » se lit.
 *
 * NON EXPORTÉS, et délibérément : rien hors de ce fichier ne les importait, et
 * un export non-composant à côté d'un composant casse le Fast Refresh — le
 * fichier entier se recharge à chaque frappe au lieu de se rafraîchir. Si un
 * jour un test doit les atteindre, la place est `ui/src/lib/`, pas un export
 * de complaisance ici.
 */
type FaitReunion = { valeur: string; libelle: string; seul: string }

function faitsDe(reunion: Reunion, t: Traduire): FaitReunion[] {
  const jour = formatDay(reunion.date, t)
  const langue = reunion.langue?.toUpperCase() || ''
  const n = reunion.nSpeakers
  const nonClasse = t('common.unclassified')
  return [
    { valeur: jour, libelle: t('meeting.fact.date'), seul: jour },
    { valeur: reunion.duree || '—', libelle: t('meeting.fact.duration'), seul: reunion.duree || t('common.unknownDuration') },
    { valeur: langue || '—', libelle: t('meeting.fact.language'), seul: langue || t('common.unknownLanguage') },
    {
      valeur: n === null || n === undefined ? '—' : String(n),
      libelle: t('meeting.fact.speakers'),
      seul: n === null || n === undefined
        ? t('meeting.fact.unknownSpeakers')
        : t(pluralKey('meeting.speakers.count', n), { n }),
    },
    { valeur: reunion.projet || nonClasse, libelle: t('meeting.fact.project'), seul: reunion.projet || nonClasse },
  ]
}

/** Onglets où l'on LIT — l'en-tête s'y efface au profit du contenu. */
const ONGLETS_DE_LECTURE: readonly MeetingTab[] = ['cr', 'transcript']

function transcriptTraits(item: LibraryItem, t: Traduire): string[] {
  return [
    item.duration || t('common.unknownDuration'),
    item.language?.toUpperCase() || t('common.unknownLanguage'),
    item.diarization
      ? t(pluralKey('meeting.speakers.count', item.n_speakers ?? 0), { n: item.n_speakers ?? '?' })
      : t('meeting.trait.notDiarized'),
    item.profiled ? t('meeting.trait.profiled') : t('meeting.trait.noProfile'),
  ]
}

export function MeetingView({
  reunion, onBack, onVoirDoublons, onChanged, initialTab, onTabChange,
}: Props) {
  const { t } = useLang()
  // Lecture seule (partage reçu) : aucune action d'écriture ; seul le
  // propriétaire du projet de la réunion la partage.
  const { lectureSeule, peutPartager } = usePartage()
  const [partageOuvert, setPartageOuvert] = useState(false)
  const hasReport = reunion.comptesRendus.length > 0
  // L'onglet vient de l'URL s'il y est, sinon du contenu disponible.
  const [tab, setTabInterne] = useState<MeetingTab>(
    (ONGLETS as readonly string[]).includes(initialTab ?? '')
      ? (initialTab as MeetingTab)
      : hasReport ? 'cr' : 'transcript')

  // Les faits de la réunion sont une option (`/api/config`, `faits`) : tant qu'elle n'est pas
  // dite activée — configuration en cours de lecture ou illisible comprise —, l'onglet
  // « Faits » n'existe pas, et un lien qui y mène ouvre l'onglet par défaut.
  const [faitsActives, setFaitsActives] = useState(false)
  useEffect(() => {
    let courant = true
    getConfig().then((config) => { if (courant) setFaitsActives(config.faits === true) })
      .catch(() => { if (courant) setFaitsActives(false) })
    return () => { courant = false }
  }, [])
  const ongletVisible: MeetingTab = tab === 'faits' && !faitsActives ? (hasReport ? 'cr' : 'transcript') : tab

  // Un seul point de changement, qui remonte au parent. Cinq appels directs à
  // `setTabInterne` laisseraient l'URL mentir dès qu'on en oublie un.
  const setTab = useCallback((suivant: MeetingTab) => {
    setTabInterne(suivant)
    onTabChange?.(suivant)
  }, [onTabChange])

  // Le parent peut imposer un onglet APRÈS le montage — c'est ce que fait le
  // bouton retour du navigateur.
  //
  // Sans cette synchronisation, `initialTab` n'était lu qu'à l'initialisation :
  // un retour arrière changeait l'URL et laissait l'écran sur l'onglet
  // précédent. Trouvé À L'ÉCRAN, aucun test ne l'attrapait — le même piège que
  // pour la réunion, traité là et oublié ici.
  useEffect(() => {
    if (!initialTab || !(ONGLETS as readonly string[]).includes(initialTab)) return
    setTabInterne((precedent) =>
      precedent === initialTab ? precedent : (initialTab as MeetingTab))
  }, [initialTab])
  const [activeReport, setActiveReport] = useState(reunion.comptesRendus[0]?.name ?? '')
  const [activeTranscript, setActiveTranscript] = useState(reunion.retenu.name)
  const [activeTimestamp, setActiveTimestamp] = useState<string | null>(null)
  const [reportText, setReportText] = useState<string | null>(null)
  const [reservesOpen, setReservesOpen] = useState(false)
  const [metas, setMetas] = useState<Record<string, Record<string, unknown>>>({})
  const [error, setError] = useState('')
  // Sensibilité de la réunion : lue sur le transcript retenu (source de vérité),
  // modifiable dans l'onglet Informations. MÉTADONNÉE — aucun effet exécution.
  const [sensibilite, setSensibilite] = useState('standard')
  const [capture, setCapture] = useState<Capture | null>(null)
  const [erreurCR, setErreurCR] = useState('')
  const [sensibiliteEnCours, setSensibiliteEnCours] = useState(false)
  const [beginReport, isCurrentReport, invalidateReport] = useLatestRequest()
  const [beginMetas, isCurrentMetas, invalidateMetas] = useLatestRequest()

  useEffect(() => {
    const newest = reunion.comptesRendus[0]?.name ?? ''
    if (!newest) {
      if (activeReport) setActiveReport('')
      return
    }
    if (!reunion.comptesRendus.some((item) => item.name === activeReport)) {
      setActiveReport(newest)
    }
  }, [activeReport, reunion.comptesRendus])

  // Pendant exact de l'effet ci-dessus, pour les TRANSCRIPTS. Il manquait :
  // après archivage du transcript affiché, la liste se rafraîchissait bien (le
  // document disparaissait des endpoints) mais `activeTranscript` gardait son
  // nom. L'onglet continuait donc d'afficher un document qui n'existe plus —
  // le seul endroit où le constat « un archivé reste actif » tenait vraiment.
  useEffect(() => {
    if (!reunion.transcripts.some((item) => item.name === activeTranscript)) {
      setActiveTranscript(reunion.retenu.name)
    }
  }, [activeTranscript, reunion.transcripts, reunion.retenu.name])

  const documents = useMemo(
    () => [...reunion.transcripts, ...reunion.comptesRendus],
    [reunion.transcripts, reunion.comptesRendus],
  )

  /**
   * La portée d'un document de CETTE réunion, d'après le document lui-même.
   *
   * La réunion n'expose pas de projet exploitable — `reunion.projet` est un
   * libellé humain, pas l'ULID que le backend attend. Chaque `LibraryItem`
   * porte en revanche son `project_id`, et c'est la seule source juste : rien
   * n'oblige la vue à être ouverte depuis le projet du document.
   */
  const porteeDe = useCallback((name: string) => porteeDocument(
    documents.find((item) => item.name === name)?.project_id), [documents])

  useEffect(() => {
    if (!activeReport) { invalidateReport(); return }
    const request = beginReport()
    setReportText(null); setError('')
    getTranscript(activeReport, porteeDe(activeReport))
      .then((detail) => {
        if (!isCurrentReport(request)) return
        setReportText(detail.body ?? detail.content ?? '')
        setMetas((current) => ({ ...current, [activeReport]: detail.meta || {} }))
      })
      .catch((reason) => {
        if (isCurrentReport(request)) setError(t('meeting.error.reportUnreadable', { reason: String(reason) }))
      })
    return () => invalidateReport(request)
  }, [activeReport, beginReport, invalidateReport, isCurrentReport, porteeDe, t])

  useEffect(() => {
    const missing = documents.filter((item) => (item.warnings || 0) > 0 && !(item.name in metas))
    if (!missing.length) { invalidateMetas(); return }
    const request = beginMetas()
    Promise.all(missing.map((item) =>
      getTranscript(item.name, porteeDocument(item.project_id))
        .then((detail) => [item.name, detail.meta || {}] as const)
        .catch(() => null),
    )).then((results) => {
      if (!isCurrentMetas(request)) return
      const additions = Object.fromEntries(
        results.filter(Boolean) as (readonly [string, Record<string, unknown>])[],
      )
      if (Object.keys(additions).length) setMetas((current) => ({ ...current, ...additions }))
    })
    return () => invalidateMetas(request)
  }, [beginMetas, documents, invalidateMetas, isCurrentMetas, metas])

  // La sensibilité est celle de la RÉUNION : le niveau effectif que le serveur applique à
  // toutes ses versions (`sensitivity_effective`), lu par le transcript retenu. Le niveau
  // noté dans son `.md` (`meta.sensitivity`) ne sert que de repli à un serveur plus ancien.
  useEffect(() => {
    let vivant = true
    getTranscript(reunion.retenu.name, porteeDocument(reunion.retenu.project_id))
      .then((detail) => {
        if (!vivant) return
        setSensibilite(normaliserSensibilite(detail.sensitivity_effective ?? detail.meta?.sensitivity))
        setCapture(lireCapture(detail.meta?.capture))
        const cause = detail.meta?.report_error
        setErreurCR(typeof cause === 'string' ? cause : '')
      })
      .catch(() => { /* lecture best-effort : le défaut « standard » tient */ })
    return () => { vivant = false }
  }, [reunion.retenu.name, reunion.retenu.project_id])

  async function changerSensibilite(valeur: string) {
    const precedent = sensibilite
    setSensibilite(valeur)          // optimiste : l'UI répond tout de suite
    setSensibiliteEnCours(true)
    setError('')
    try {
      // Le changement vaut pour toute la réunion ; s'affiche le niveau que le serveur applique.
      const applique = await updateSensitivity(
        reunion.retenu.name, valeur, porteeDocument(reunion.retenu.project_id),
      )
      setSensibilite(normaliserSensibilite(applique.sensitivity))
      onChanged?.()
    } catch (raison) {
      setSensibilite(precedent)     // échec : on revient à l'état serveur
      setError(t('meeting.error.sensitivityNotSaved', { reason: String(raison) }))
    } finally {
      setSensibiliteEnCours(false)
    }
  }

  const reserves: Reserve[] = useMemo(() => {
    const out: Reserve[] = []
    for (const item of documents) {
      const meta = metas[item.name]
      if (meta) out.push(...reservesDe(documentLabel(item, t), meta, t))
    }
    return out
  }, [documents, metas, t])

  // En lecture, l'en-tête plein poussait le premier mot du
  // document sous la ligne de flottaison. Il s'efface alors en une barre.
  const lecture = ONGLETS_DE_LECTURE.includes(tab)
  const faits = useMemo(() => faitsDe(reunion, t), [reunion, t])

  const title = displayMeetingTitle(reunion.titre, reunion.source)
  const activeReportItem = reunion.comptesRendus.find((item) => item.name === activeReport)
  const reportGenre = typeof metas[activeReport]?.genre === 'string'
    ? metas[activeReport].genre
    : null
  // Provenance discrète : le modèle réellement servi (front-matter `model`), pour
  // que l'utilisateur sache QUI a rédigé (Qwen local, Mistral, gpt-5.6-luna…).
  const reportModel = typeof metas[activeReport]?.model === 'string'
    ? (metas[activeReport].model as string)
    : null

  function showTranscript(name: string) {
    setActiveTranscript(name)
    setActiveTimestamp(null)
    setTab('transcript')
  }

  function showReport(name: string) {
    setActiveReport(name)
    setTab('cr')
  }

  function showFactSource(document: string, timestamp: string) {
    setActiveTranscript(document || reunion.retenu.name)
    setActiveTimestamp(timestamp)
    setTab('transcript')
  }

  function showReportSource(timestamp: string) {
    setActiveTranscript(
      activeReportItem?.source_transcript || reunion.retenu.name,
    )
    setActiveTimestamp(timestamp)
    setTab('transcript')
  }

  async function handleArchiveDocument(name: string) {
    setError('')
    // La portée vient du document archivé. Ces deux routes SONT bornées côté
    // backend : sans elle, la portée retombait sur la boîte de réception et
    // archiver un document classé ailleurs rendait 404 — en production.
    const portee = porteeDe(name)
    const isReport = name.endsWith('-cr.md') || name.includes('-cr-')
    let planDocs: { name: string; title: string }[] = [{ name, title: name }]
    try {
      const plan = await getArchivePlan(name, portee)
      if (plan.documents?.length) {
        planDocs = plan.documents.map((d) => ({ name: d.name, title: d.title || d.name }))
      }
    } catch {
      // Sondage best-effort
    }

    const docList = planDocs.map((d) => ` • ${d.title}`).join('\n')
    const typeLabel = isReport ? t('meeting.archive.reportKind') : t('meeting.archive.transcriptKind')
    const promptMsg = planDocs.length > 1
      ? t('meeting.archive.confirmMany', { kind: typeLabel, name, list: docList })
      : t('meeting.archive.confirmOne', { kind: typeLabel, name })

    if (!confirm(promptMsg)) return

    try {
      await archiveTranscript(name, portee)
      onChanged?.()
      const remaining = documents.filter((d) => !planDocs.some((pd) => pd.name === d.name))
      if (remaining.length === 0) {
        onBack()
      }
    } catch (e) {
      setError(t('meeting.error.archiveFailed', { error: String(e) }))
    }
  }

  return (
    <section className="meeting-workspace">
      <header className={lecture ? 'meeting-hero compact' : 'meeting-hero'}>
        <div className="meeting-hero-top">
          <button type="button" className="btn" onClick={onBack}><Icon name="arrow-left" /> {t('meeting.back')}</button>
          <div className="meeting-scope-actions" aria-label={t('meeting.actions.aria')}>
            {peutPartager(reunion.retenu.project_id) && (
              <button type="button" className="btn" onClick={() => setPartageOuvert(true)}>
                {t('share.button')}
              </button>
            )}
            {!lectureSeule && onVoirDoublons && reunion.transcripts.length > 1 && (
              <button type="button" className="btn" onClick={onVoirDoublons}>{t('meeting.compareVersions')}</button>
            )}
          </div>
        </div>
        <div className="meeting-hero-main">
          <div>
            {!lecture && <span className="meeting-eyebrow">{t('meeting.eyebrow')}</span>}
            <h1>{title}</h1>
            {lecture ? (
              <p className="meeting-hero-line" aria-label={t('meeting.mainInfo.aria')}>
                {faits.map((fait) => fait.seul).join(' · ')}
              </p>
            ) : (
              reunion.source && <p className="meeting-hero-source">{reunion.source}</p>
            )}
          </div>
          {!lecture && (
            <div className="meeting-hero-facts" aria-label={t('meeting.mainInfo.aria')}>
              {faits.map((fait) => (
                <span key={fait.libelle}>
                  <strong>{fait.valeur}</strong><small>{fait.libelle}</small>
                </span>
              ))}
            </div>
          )}
        </div>
      </header>

      {error && <p className="error banner"><Icon name="error" /> {error}</p>}

      {reunion.avertissements > 0 && (
        <aside className="meeting-reserves" aria-label={t('meeting.reserves.aria')}>
          <button type="button" aria-expanded={reservesOpen} onClick={() => setReservesOpen((value) => !value)}>
            <span><Icon name="alert" /> {t(pluralKey('meeting.reserves.count', reunion.avertissements), { n: reunion.avertissements })}</span>
            <span className="muted small">{reservesOpen ? t('meeting.reserves.hide') : t('meeting.reserves.show')}</span>
          </button>
          {reservesOpen && (
            reserves.length ? (
              <ul>
                {reserves.map((reserve, index) => (
                  <li key={`${reserve.objet}-${index}`}>
                    <strong>{reserve.objet}</strong>
                    <span>{reserve.texte}</span>
                    <small>{reserve.detail.slice(0, 3).join(' · ')}</small>
                  </li>
                ))}
              </ul>
            ) : <p className="muted small">{t('meeting.reserves.loading')}</p>
          )}
        </aside>
      )}

      <div className="meeting-tabs" role="tablist" aria-label={t('meeting.tabs.aria')}>
        <button type="button" role="tab" aria-selected={ongletVisible === 'cr'} onClick={() => setTab('cr')}>
          {t('common.docKind.report')} {reunion.comptesRendus.length > 0 && <span>{reunion.comptesRendus.length}</span>}
        </button>
        <button type="button" role="tab" aria-selected={ongletVisible === 'transcript'} onClick={() => setTab('transcript')}>
          {t('common.docKind.transcript')} <span>{reunion.transcripts.length}</span>
        </button>
        {faitsActives && (
          <button type="button" role="tab" aria-selected={ongletVisible === 'faits'} onClick={() => setTab('faits')}>
            {t('meeting.tab.facts')}
          </button>
        )}
        <button type="button" role="tab" aria-selected={ongletVisible === 'versions'} onClick={() => setTab('versions')}>
          {t('meeting.tab.versions')} <span>{documents.length}</span>
        </button>
        <button type="button" role="tab" aria-selected={ongletVisible === 'info'} onClick={() => setTab('info')}>
          {t('meeting.tab.info')}
        </button>
      </div>

      <div className="meeting-panels">
        <section role="tabpanel" aria-label={t('common.docKind.report')} hidden={ongletVisible !== 'cr'} className="meeting-panel report-panel">
          {!hasReport ? (
            <div className="empty-state compact">
              <span className="empty-icon"><Icon name="report" /></span>
              <strong>{t('meeting.report.empty.title')}</strong>
              <p className="muted">{t('meeting.report.empty.detail')}</p>
              <button type="button" className="btn accent llm" onClick={() => setTab('transcript')}>{t('meeting.report.empty.action')}</button>
            </div>
          ) : (
            <>
              <div className="report-toolbar">
                <div>
                  <span className="action-scope">{t('common.activeDocument')}</span>
                  <strong>{activeReportItem ? formatVersion(activeReportItem, t) : t('common.docKind.report')}</strong>
                  <small>{activeReportItem?.name} · {t('meeting.report.fromSource', { source: activeReportItem?.source_transcript ?? t('meeting.report.fromSource.fallbackThis') })}</small>
                </div>
                <div className="report-toolbar-actions">
                  {reunion.comptesRendus.length > 1 && (
                    <div className="report-switcher" aria-label={t('meeting.report.versions.aria')}>
                      {reunion.comptesRendus.map((item, index) => (
                        <button key={item.name} type="button" className={activeReport === item.name ? 'on' : ''}
                                aria-label={t('meeting.report.showVersion.aria', { index: index + 1, date: formatVersion(item, t), name: item.name })}
                                onClick={() => showReport(item.name)}>{formatVersion(item, t)} · {index + 1}</button>
                      ))}
                    </div>
                  )}
                  {activeReport && !lectureSeule && (
                    <button type="button" className="btn danger-subtle" onClick={() => handleArchiveDocument(activeReport)}>
                      <Icon name="archive" /> {t('meeting.report.archive')}
                    </button>
                  )}
                </div>
              </div>
              {reportText === null ? <div className="report-loading skeleton" aria-label={t('meeting.report.loading.aria')} />
                : (
                  <ReportView
                    content={reportText}
                    genre={reportGenre}
                    model={reportModel}
                    onTimestamp={showReportSource}
                  />
                )}
            </>
          )}
        </section>

        <section role="tabpanel" aria-label={t('common.docKind.transcript')} hidden={ongletVisible !== 'transcript'} className="meeting-panel transcript-panel">
          <TranscriptReader
            name={activeTranscript}
            documentScope={porteeDe(activeTranscript)}
            title={title}
            onBack={onBack}
            hideBack
            embedded
            initialTimestamp={activeTimestamp}
            lancer
            onReportCreated={() => onChanged?.()}
            onArchive={lectureSeule ? undefined : handleArchiveDocument}
          />
        </section>

        {faitsActives && (
          <section role="tabpanel" aria-label={t('meeting.facts.aria')} hidden={ongletVisible !== 'faits'} className="meeting-panel facts-panel">
            {ongletVisible === 'faits' && (
              <EclFaits meetingId={reunion.cle} viewScope={porteeDe(reunion.retenu.name)} onSource={showFactSource}
                ultra={sensibilite === 'ultra_confidential'}
                lectureSeule={lectureSeule} />
            )}
          </section>
        )}

        <section role="tabpanel" aria-label={t('meeting.tab.versions')} hidden={ongletVisible !== 'versions'} className="meeting-panel versions-panel">
          <div className="version-group">
            <div className="version-heading"><h2>{t('meeting.versions.transcripts')}</h2><span>{reunion.transcripts.length}</span></div>
            <ul className="version-list">
              {reunion.transcripts.map((item) => (
                <li key={item.name} className={item.name === activeTranscript ? 'active' : ''}>
                  <div><strong>{formatVersion(item, t)}</strong><small>{item.name} · {transcriptTraits(item, t).join(' · ')}</small></div>
                  <div className="version-actions">
                    {item.name === activeTranscript
                      ? <Badge tone="accent">{t('common.activeDocument')}</Badge>
                      : <button type="button" className="btn sm" onClick={() => showTranscript(item.name)}>{t('meeting.versions.show')}</button>}
                    {!lectureSeule && (
                      <button type="button" className="btn sm danger-subtle" onClick={() => handleArchiveDocument(item.name)} title={t('meeting.versions.archiveTranscript')}>
                        <Icon name="archive" /> {t('common.archive')}
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div className="version-group">
            <div className="version-heading"><h2>{t('meeting.versions.reports')}</h2><span>{reunion.comptesRendus.length}</span></div>
            {hasReport ? (
              <ul className="version-list">
                {reunion.comptesRendus.map((item) => (
                  <li key={item.name} className={item.name === activeReport ? 'active' : ''}>
                    <div><strong>{formatVersion(item, t)}</strong><small>{item.name} · {t('meeting.report.fromSource', { source: item.source_transcript || t('meeting.report.fromSource.fallbackMeeting') })}</small></div>
                    <div className="version-actions">
                      {item.name === activeReport
                        ? <Badge tone="llm">{t('common.activeDocument')}</Badge>
                        : <button type="button" className="btn sm" onClick={() => showReport(item.name)}>{t('meeting.versions.show')}</button>}
                      {!lectureSeule && (
                        <button type="button" className="btn sm danger-subtle" onClick={() => handleArchiveDocument(item.name)} title={t('meeting.report.archive')}>
                          <Icon name="archive" /> {t('common.archive')}
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            ) : <p className="muted">{t('meeting.versions.noReport')}</p>}
          </div>

          {!lectureSeule && onVoirDoublons && reunion.transcripts.length > 1 && (
            <div className="version-comparison">
              <p>{t('meeting.versions.compareHint')}</p>
              <button type="button" className="btn" onClick={onVoirDoublons}>{t('meeting.versions.compareInMaintenance')}</button>
            </div>
          )}
        </section>

        <section role="tabpanel" aria-label={t('meeting.tab.info')} hidden={ongletVisible !== 'info'} className="meeting-panel info-panel">
          <dl className="meeting-info-grid">
            <div><dt>{t('meeting.info.id')}</dt><dd><code>{reunion.cle}</code></dd></div>
            <div><dt>{t('meeting.fact.project')}</dt><dd>{reunion.projet || t('common.unclassified')}</dd></div>
            <div><dt>{t('meeting.info.source')}</dt><dd>{reunion.source || t('common.notProvided')}</dd></div>
            <div><dt>{t('meeting.fact.date')}</dt><dd>{formatDay(reunion.date, t)}</dd></div>
            <div><dt>{t('meeting.fact.duration')}</dt><dd>{reunion.duree || t('common.notProvided')}</dd></div>
            <div><dt>{t('meeting.fact.language')}</dt><dd>{reunion.langue?.toUpperCase() || t('common.notProvided')}</dd></div>
            {erreurCR && (
              <div><dt>{t('meeting.info.reportError')}</dt><dd role="note">{erreurCR}</dd></div>
            )}
            {capture && (
              <div><dt>{t('meeting.info.capture')}</dt><dd>
                {[capture.application, capture.mode && t(capture.mode === 'visio' ? 'meeting.info.capture.visio' : 'meeting.info.capture.replay')]
                  .filter(Boolean).join(' · ')}
              </dd></div>
            )}
            <div><dt>{t('meeting.info.sensitivity')}</dt><dd>
              {lectureSeule ? t(sensitivityLabelKey(sensibilite)) : (
                <>
                  <select className="option-select" value={sensibilite}
                          disabled={sensibiliteEnCours} aria-label={t('meeting.info.sensitivity')}
                          onChange={(event) => changerSensibilite(event.target.value)}>
                    {SENS_VALEURS.map((valeur) => (
                      <option key={valeur} value={valeur}>{t(sensitivityLabelKey(valeur))}</option>
                    ))}
                  </select>
                  <small className="muted">{t('meeting.info.sensitivity.hint')}</small>
                </>
              )}
            </dd></div>
          </dl>
          {!lectureSeule && <div className="action-scope-card finops-card">
            <span className="action-scope">{t('finops.title')}</span>
            {activeReport && (
              <FinopsSection name={activeReport} viewScope={porteeDe(activeReport)}
                             label={t('finops.report')} />
            )}
            <FinopsSection name={reunion.retenu.name} viewScope={porteeDe(reunion.retenu.name)}
                           label={t('finops.transcription')} />
          </div>}
          {!lectureSeule && <div className="action-scope-card">
            <span className="action-scope">{t('meeting.info.actionsTitle')}</span>
            <p className="muted small">{t('meeting.info.actionsDetail')}</p>
            {onVoirDoublons && reunion.transcripts.length > 1 && (
              <button type="button" className="btn" onClick={onVoirDoublons}>{t('meeting.compareVersions')}</button>
            )}
          </div>}
        </section>
      </div>
      {partageOuvert && (
        <PartageDialog type="reunion" cible={reunion.cle} nom={title}
                       onClose={() => setPartageOuvert(false)} />
      )}
    </section>
  )
}
