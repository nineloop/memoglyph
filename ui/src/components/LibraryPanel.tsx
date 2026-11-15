import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import type { LibraryQuery, Meeting, Project } from '../api'
import { archiveMeeting, getLanguages, getMeetings, moveMeeting, porteeDocument } from '../api'
import { libelleProjet } from '../lib/libelleProjet'
import { displayMeetingTitle, reunionDepuisApi, voisines } from '../lib/meetings'
import { FilAriane } from './FilAriane'
import { NavigationReunion } from './NavigationReunion'
import { MeetingView } from './MeetingView'
import { Icon } from './Icon'
import { useLatestRequest } from '../lib/latestRequest'
import { Badge, Button, EmptyState, EtatEchec, Skeleton } from '../design/primitives'
import { derniereEcoute, useAudio } from '../audio/AudioProvider'
import { useLang } from '../lib/langContext'
import { useConfirmer } from '../design/Confirmation'
import { phraseDErreur } from '../lib/erreurs'
import { usePartage } from '../lib/partage'
import { pluralKey } from '../lib/i18n'
import type { Lang, Traduire } from '../lib/i18n'
import { formaterJour } from '../lib/dates'
import type { MessageKey } from '../lib/messages'

function mmss(total: number): string {
  const s = Math.floor(total % 60)
  const m = Math.floor(total / 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

const SORTS: { key: LibraryQuery['sort']; label: MessageKey }[] = [
  { key: 'modified', label: 'library.sort.recent' },
  { key: 'title', label: 'library.sort.title' },
  { key: 'duration', label: 'library.sort.duration' },
  { key: 'language', label: 'library.sort.language' },
]

type LibProps = {
  reloadKey: number
  viewScope?: string
  projects?: Project[]
  onChanged?: () => void
  /** Bascule vers l'onglet Doublons — la comparaison n'appartient PAS à la navigation. */
  onVoirDoublons?: () => void
  /** Action de l'état vide : la navigation reste pilotée par le shell. */
  onTranscrire?: () => void
  /** Document créé à ouvrir une fois son agrégat réunion chargé. */
  openDocumentName?: string | null
  onInitialMeetingOpened?: () => void
  /** Réunion à ouvrir, par `meeting_id` — vient de l'URL. */
  openMeetingId?: string | null
  /** Remonte la réunion ouverte au parent, pour que l'URL la porte. */
  onMeetingChanged?: (id: string | null) => void
  /** Onglet de réunion imposé par l'URL, et son écho vers le parent. */
  meetingTab?: string | null
  onMeetingTabChange?: (onglet: string) => void
  /** Ouvre un projet (galerie, fil d'Ariane) : le même chemin que le panneau des projets. */
  onSelectProject?: (id: string) => void
  /** Nom du projet consulté, pour l'en-tête et le fil d'Ariane. */
  titre?: string
  /**
   * Avec qui le projet consulté est partagé. `undefined` : on ne sait pas, ou le projet ne se
   * partage pas (réception, « tous mes projets », lecture seule) — la ligne n'est alors pas écrite.
   */
  partageAvec?: string[]
  /** Ouvre le partage du projet consulté (propriétaire seulement). */
  onPartager?: () => void
}

function formatDate(value: string, t: Traduire, lang: Lang): string {
  return formaterJour(value, lang) ?? (value || t('common.unknownDate'))
}

export function LibraryPanel({
  reloadKey, viewScope = '', projects = [], onChanged, onVoirDoublons, onTranscrire,
  openDocumentName, onInitialMeetingOpened, openMeetingId, onMeetingChanged,
  meetingTab, onMeetingTabChange, onSelectProject, titre, partageAvec, onPartager,
}: LibProps) {
  const { t, lang } = useLang()
  const confirmer = useConfirmer()
  // Lecture seule (partage reçu) : ni classer, ni archiver.
  const { lectureSeule } = usePartage()
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [languages, setLanguages] = useState<string[]>([])
  const [q, setQ] = useState('')
  const [dq, setDq] = useState('') // q débouncé
  const [sort, setSort] = useState<LibraryQuery['sort']>('modified')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [language, setLanguage] = useState('')
  const [diarized, setDiarized] = useState(false)
  const [profiled, setProfiled] = useState(false)
  const [selected, setSelected] = useState<string | null>(openMeetingId ?? null)

  // Toute ouverture ou fermeture remonte au parent, qui la sérialise dans
  // l'URL. Passer par une fonction unique plutôt que d'appeler `setSelected`
  // en cinq endroits : un seul de ces endroits oublié laisserait l'URL mentir.
  const choisir = useCallback((id: string | null) => {
    setSelected(id)
    onMeetingChanged?.(id)
  }, [onMeetingChanged])

  // Le parent impose une réunion (retour arrière, lien partagé).
  useEffect(() => {
    setSelected((precedent) =>
      precedent === (openMeetingId ?? null) ? precedent : openMeetingId ?? null)
  }, [openMeetingId])
  const [loading, setLoading] = useState(true)
  // `error` ne dit que l'échec d'une ACTION (déplacer, archiver) : la liste reste affichée.
  const [error, setError] = useState('')
  const idListe = useId()   // relie chaque ligne à ses faits (date, durée, langue, locuteurs), lus avec leur libellé
  // L'échec d'une ACTION (déplacer, archiver) : un état à part, que le rechargement de la liste n'efface pas.
  const [erreurAction, setErreurAction] = useState('')
  // L'échec du CHARGEMENT remplace la liste : « vide » ne s'affiche que quand le chargement a abouti.
  const [echecChargement, setEchecChargement] = useState(false)
  // Le nombre de réunions n'est affirmé qu'après une première réponse : avant, il est inconnu (pas « 0 »).
  const [dejaCharge, setDejaCharge] = useState(false)
  // L'échec qui SUIT le « Réessayer » de la personne reçoit le focus (le bouton se remonte) ; un autre ne le prend pas.
  const [apresReprise, setApresReprise] = useState(false)
  const [relance, setRelance] = useState(0)
  const repriseDemandee = useRef(false)
  const reessayer = useCallback(() => { repriseDemandee.current = true; setRelance((n) => n + 1) }, [])
  const [actionMenu, setActionMenu] = useState<string | null>(null)
  const [beginMeetings, isCurrentMeetings, invalidateMeetings] = useLatestRequest()

  // Débounce de la recherche
  useEffect(() => {
    const t = setTimeout(() => setDq(q), 250)
    return () => clearTimeout(t)
  }, [q])

  useEffect(() => { getLanguages().then(setLanguages).catch(() => {}) }, [reloadKey])

  useEffect(() => {
    const request = beginMeetings()
    setLoading(true)
    setEchecChargement(false)
    setApresReprise(repriseDemandee.current)
    repriseDemandee.current = false
    getMeetings({
      q: dq, sort, order, language, viewScope,
      diarized: diarized || undefined, profiled: profiled || undefined,
    })
      .then((r) => {
        if (isCurrentMeetings(request)) { setMeetings(r); setError(''); setDejaCharge(true) }
      })
      .catch(() => {
        if (isCurrentMeetings(request)) setEchecChargement(true)
      })
      .finally(() => { if (isCurrentMeetings(request)) setLoading(false) })
    return () => invalidateMeetings(request)
  }, [
    beginMeetings, diarized, dq, invalidateMeetings, isCurrentMeetings, language,
    order, profiled, reloadKey, relance, sort, viewScope,
  ])

  useEffect(() => {
    if (!actionMenu) return
    const close = () => setActionMenu(null)
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    window.addEventListener('click', close)
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      window.removeEventListener('click', close)
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [actionMenu])

  // La portée vient du document RETENU de la réunion, pas de `viewScope` : une
  // réunion listée en portée `all` appartient à un projet précis, et c'est ce
  // projet-là qui la localise.
  async function move(meetingId: string, target: string, portee: string) {
    setErreurAction('')
    try {
      await moveMeeting(meetingId, target, portee)
      setActionMenu(null)
    } catch (e) {
      setErreurAction(phraseDErreur(t, lang, e, t('library.moveFailed')))
    }
    onChanged?.()
  }

  async function remove(meetingId: string, title: string, portee: string) {
    const ok = await confirmer({ titre: t('library.archiveMeeting.titre', { titre: title }), phrase: t('library.archiveMeeting.confirm'), libelleAction: t('library.menu.archive') })
    if (!ok) return
    setErreurAction('')
    try {
      await archiveMeeting(meetingId, portee)
    } catch (e) {
      setErreurAction(phraseDErreur(t, lang, e, t('library.archiveIncomplete')))
    }
    onChanged?.()
  }

  // Une ligne = une RÉUNION, pas un document. La clé stable est `meeting_id` ;
  // le nom du média reste un simple libellé humain.
  const reunions = useMemo(() => meetings.map(reunionDepuisApi), [meetings])
  const count = reunions.length
  const projetsGalerie = useMemo(() => projects.filter((p) => p.id), [projects])

  const audio = useAudio()
  // Accueil « reprendre » : le dernier document écouté, s'il vit dans une
  // réunion visible ici. Masqué quand un lecteur tourne déjà — le mini-lecteur
  // porte alors « en cours d'écoute » et un doublon n'apporterait rien.
  const reprise = useMemo(() => {
    if (audio.track) return null
    const rec = derniereEcoute()
    if (!rec) return null
    const cible = reunions.find((m) =>
      [...m.transcripts, ...m.comptesRendus].some((d) => d.name === rec.name))
    return cible ? { rec, cible } : null
  }, [audio.track, reunions])

  function reprendre() {
    if (!reprise) return
    // Reprise à la position sauvegardée (aucun `startAt`) et lecture immédiate.
    audio.play({ name: reprise.rec.name, scope: reprise.rec.scope, title: reprise.rec.title },
      { autoplay: true })
    onMeetingTabChange?.('transcript')
    choisir(reprise.cible.cle)
  }

  useEffect(() => {
    if (!openDocumentName || loading) return
    const target = reunions.find((meeting) =>
      [...meeting.transcripts, ...meeting.comptesRendus].some(
        (document) => document.name === openDocumentName,
      ),
    )
    if (target) choisir(target.cle)
    onInitialMeetingOpened?.()
  }, [loading, openDocumentName, onInitialMeetingOpened, reunions, choisir])

  const filtresActifs = Boolean(dq || language || diarized || profiled)
  function reinitialiser() {
    setQ(''); setDq(''); setLanguage(''); setDiarized(false); setProfiled(false)
  }

  const ouverte = selected ? reunions.find((m) => m.cle === selected) : undefined
  if (ouverte) {
    // Le fil d'Ariane et les flèches suivent le projet de la RÉUNION, pas le projet consulté :
    // ouverte depuis « Tous mes projets », elle garde pour voisines celles de son projet.
    const idProjet = ouverte.retenu.project_id
    const projetDeLaReunion = projects.find((p) => p.id === idProjet)
    const { precedente, suivante } = voisines(reunions, ouverte.cle)
    const voisine = (r?: typeof precedente) => r && { cle: r.cle, titre: displayMeetingTitle(r.titre, r.source) }
    return <MeetingView key={ouverte.cle} reunion={ouverte} onBack={() => choisir(null)}
                        initialTab={meetingTab} onTabChange={onMeetingTabChange}
                        onVoirDoublons={onVoirDoublons} onChanged={onChanged}
                        ariane={
                          <FilAriane
                            // Un projet reçu n'est pas dans `projects` : on garde son niveau avec le nom de la réunion.
                            projet={idProjet ? { id: idProjet, nom: libelleProjet(projetDeLaReunion?.name ?? ouverte.projet, t, projetDeLaReunion?.kind) } : undefined}
                            reunion={displayMeetingTitle(ouverte.titre, ouverte.source)}
                            surBibliotheque={() => { choisir(null); onSelectProject?.('all') }}
                            surProjet={() => { choisir(null); if (idProjet) onSelectProject?.(idProjet) }} />
                        }
                        navigation={
                          <NavigationReunion precedente={voisine(precedente)} suivante={voisine(suivante)}
                                             surAller={choisir} />
                        } />
  }

  return (
    <section className="card library-card">
      <FilAriane
        projet={titre && viewScope !== 'all' && viewScope !== 'shared' ? { id: viewScope, nom: titre } : undefined}
        surBibliotheque={() => onSelectProject?.('all')}
        surProjet={() => onSelectProject?.(viewScope)} />
      <div className="library-heading">
        <div>
          <h1>{titre ?? t('library.title')}</h1>
          <p className="muted small">
            {/* Un chargement qui a échoué ne dit pas « 0 réunion » : le nombre est inconnu. */}
            {!echecChargement && dejaCharge && t(pluralKey('library.count', count, lang), { n: count })}
            {lectureSeule && <> · {t('nav.project.readOnly')}</>}
            {partageAvec && <> · {partageAvec.length > 0
              ? t('nav.project.sharedWith', { noms: partageAvec.join(', ') })
              : t('nav.project.sharedNobody')}</>}
          </p>
        </div>
        <div className="library-heading-actions">
          {onPartager && (
            <button type="button" className="btn" onClick={onPartager}>{t('share.button')}</button>
          )}
          <button type="button" className="btn" onClick={() => onChanged?.()}>
            <Icon name="refresh" /> {t('nav.refresh')}
          </button>
          <input className="search library-search" type="search" aria-label={t('library.filter.aria')}
                 placeholder={t('library.filter.placeholder')} value={q}
                 onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      {error && <p className="error"><Icon name="error" /> {error}</p>}
      {erreurAction && <p className="error" role="alert"><Icon name="error" /> {erreurAction}</p>}

      {reprise && (() => {
        // Titre HUMAIN de la réunion cible plutôt que celui stocké au fil de la
        // lecture (qui pouvait être un slug, selon l'endroit d'où l'écoute est
        // partie). La cible est déjà résolue : on s'aligne sur la bibliothèque.
        const titre = displayMeetingTitle(reprise.cible.titre, reprise.cible.source)
        return (
          <button type="button" className="reprise-hero" onClick={reprendre}
                  aria-label={t('library.resume.aria', { titre, temps: mmss(reprise.rec.at) })}>
            <span className="reprise-play"><Icon name="play" /></span>
            <span className="reprise-corps">
              <span className="reprise-eyebrow">{t('library.resume.eyebrow')}</span>
              <span className="reprise-titre">{titre}</span>
            </span>
            <span className="reprise-temps">{t('library.resume.at', { temps: mmss(reprise.rec.at) })}</span>
          </button>
        )
      })()}

      {/* Galerie de projets : n'apparaît qu'en vue globale et sans filtre, comme
          un accueil premium. Le clic ouvre le projet via la même logique que le
          sélecteur de portée. En dessous, la liste triée par récence tient lieu
          de « contenus récents ». */}
      {viewScope === 'all' && !filtresActifs && onSelectProject && projetsGalerie.length > 1 && (
        <section className="project-gallery" aria-label={t('library.gallery.aria')}>
          <h3 className="gallery-title">{t('library.gallery.title')}</h3>
          <div className="gallery-grid">
            {projetsGalerie.map((projet) => (
              <button key={projet.id} type="button"
                      className={`project-card ${projet.kind === 'inbox' ? 'inbox' : ''}`}
                      onClick={() => onSelectProject(projet.id!)}>
                <span className="project-card-glyph"><Icon name="folder" /></span>
                <span className="project-card-body">
                  <span className="project-card-name">{libelleProjet(projet.name, t, projet.kind)}</span>
                  <span className="project-card-count">
                    {t(pluralKey('library.project.count', projet.count, lang), { n: projet.count })}
                  </span>
                </span>
              </button>
            ))}
          </div>
          {count > 0 && <h3 className="gallery-title recent-title">{t('library.gallery.recent')}</h3>}
        </section>
      )}

      <div className="library-tools">
        <div className="toolbar">
          <div className="seg" role="group" aria-label={t('library.sort.aria')}>
            {SORTS.map((s) => (
              <button key={s.key} type="button"
                      className={`seg-btn ${sort === s.key ? 'on' : ''}`} aria-pressed={sort === s.key}
                      onClick={() => setSort(s.key)}>{t(s.label)}</button>
            ))}
            <button type="button" className="seg-btn icon" title={t('library.order.invert')}
                    aria-label={t('library.order.aria', { sens: order === 'asc' ? t('library.order.asc') : t('library.order.desc') })}
                    onClick={() => setOrder((o) => (o === 'asc' ? 'desc' : 'asc'))}>
              {order === 'asc' ? '↑' : '↓'}
            </button>
          </div>
          <div className="filters">
            <button type="button" className={`chip sm ${diarized ? 'on' : ''}`} aria-pressed={diarized}
                    onClick={() => setDiarized((v) => !v)}><Icon name="users" /> {t('library.filter.diarized')}</button>
            <button type="button" className={`chip sm ${profiled ? 'on' : ''}`} aria-pressed={profiled}
                    onClick={() => setProfiled((v) => !v)}><Icon name="sparkles" /> {t('library.filter.profiled')}</button>
            {languages.length > 1 && (
              <select className="mini-select" aria-label={t('library.filter.language.aria')} value={language}
                      onChange={(e) => setLanguage(e.target.value)}>
                <option value="">{t('library.filter.allLanguages')}</option>
                {languages.map((l) => <option key={l} value={l}>{l.toUpperCase()}</option>)}
              </select>
            )}
          </div>
        </div>
      </div>

      {loading ? (
        <ul className="meeting-list" aria-label={t('library.loading.aria')}>
          {[0, 1, 2, 3].map((item) => (
            <li key={item} className="meeting-row skeleton-row" aria-hidden="true">
              <Skeleton w="60%" h={15} />
              <Skeleton w="90%" h={13} />
              <Skeleton w={72} h={22} radius={999} />
            </li>
          ))}
        </ul>
      )
        : echecChargement ? (
          <EtatEchec onRetry={reessayer} focusRetry={apresReprise}>{t('library.loadFailed')}</EtatEchec>
        )
        : count === 0 ? (
          // NN/g : un état vide doit dire ce qui POURRAIT s'afficher et donner
          // un chemin d'action. Deux cas distincts, deux issues différentes.
          filtresActifs ? (
            <EmptyState icon="search" title={t('library.empty.filtered.title')}
              action={<Button variant="primary" onClick={reinitialiser}>{t('library.empty.filtered.action')}</Button>}>
              {t('library.empty.filtered.detail')}
            </EmptyState>
          ) : (
            <EmptyState icon="folder" title={t('library.empty.first.title')}
              action={onTranscrire && <Button variant="primary" onClick={onTranscrire}>{t('library.empty.first.action')}</Button>}>
              {t('library.empty.first.detail')}
            </EmptyState>
          )
        )
        : (
        <ul className="meeting-list">
          {reunions.map((meeting, rang) => {
            const project = projects.find((candidate) => candidate.id === meeting.retenu.project_id)
            const isInbox = project?.kind === 'inbox'
            const hasDestinations = projects.some((candidate) => candidate.id && candidate.id !== meeting.retenu.project_id)
            const menuOpen = actionMenu === meeting.cle
            const title = displayMeetingTitle(meeting.titre, meeting.source)
            const portee = porteeDocument(meeting.retenu.project_id)
            return (
            <li key={meeting.cle} className={`meeting-row ${menuOpen ? 'menu-open' : ''}`}>
              <div className={`meeting-summary ${viewScope === 'all' || viewScope === 'shared' ? 'avec-projet' : ''}`} role="button" tabIndex={0}
                   aria-label={t('library.open.aria', { titre: title })} aria-describedby={`${idListe}-faits-${rang}`}
                   onClick={() => choisir(meeting.cle)}
                   onKeyDown={(event) => {
                     if (event.key === 'Enter' || event.key === ' ') {
                       event.preventDefault(); choisir(meeting.cle)
                     }
                   }}>
                <div className="meeting-identity">
                  <strong className="meeting-title">{title}</strong>
                  <span className="meeting-source">{meeting.source || t('library.row.sourceNone')}</span>
                </div>

                <div className="meeting-facts" id={`${idListe}-faits-${rang}`}>
                  <span><span className="sr-only">{t('library.row.date.aria')} : </span>{formatDate(meeting.date, t, lang)}</span>
                  <span><span className="sr-only">{t('library.row.duration.aria')} : </span><Icon name="clock" /> {meeting.duree || '—'}</span>
                  <span className="meeting-language"><span className="sr-only">{t('library.row.language.aria')} : </span>{meeting.langue?.toUpperCase() || '—'}</span>
                  <span><span className="sr-only">{t('library.row.speakers.aria')} : </span><Icon name="users" /> {meeting.nSpeakers ?? '—'}</span>
                  {/* Un seul projet affiché : la colonne répéterait le projet consulté. */}
                  {(viewScope === 'all' || viewScope === 'shared') && (
                    <span className="meeting-project"><span className="sr-only">{t('library.row.project.aria')} : </span><Icon name="folder" /> {libelleProjet(meeting.projet || t('common.unclassified'), t)}</span>
                  )}
                </div>

                <div className="meeting-status">
                  <Badge tone="neutral">
                    {meeting.transcripts.length > 1 ? t('library.badge.versions', { n: meeting.transcripts.length }) : t('library.badge.oneTranscript')}
                  </Badge>
                  <Badge tone={meeting.comptesRendus.length ? 'llm' : 'neutral'}>
                    {meeting.comptesRendus.length
                      ? t(pluralKey('library.badge.reports', meeting.comptesRendus.length, lang), { n: meeting.comptesRendus.length })
                      : t('library.badge.noReport')}
                  </Badge>
                  {meeting.avertissements > 0 && (
                    <span className="quality-reserve"
                          title={t('library.reserve.aria', { n: meeting.avertissements })}>
                      <Icon name="alert" /> {meeting.avertissements}
                    </span>
                  )}
                </div>
              </div>

              {!lectureSeule && <div className="meeting-actions" onClick={(event) => event.stopPropagation()}>
                {isInbox && hasDestinations && (
                  <button type="button" className="btn accent sm classify-button"
                          onClick={(event) => {
                            event.stopPropagation()
                            setActionMenu(menuOpen ? null : meeting.cle)
                          }}>
                    {t('library.classify')}
                  </button>
                )}
                <button type="button" className="icon-action"
                        aria-label={t('library.actions.aria', { titre: title })} aria-expanded={menuOpen}
                        onClick={(event) => {
                          event.stopPropagation()
                          setActionMenu(menuOpen ? null : meeting.cle)
                        }}>
                  <Icon name="more" />
                </button>
                {menuOpen && (
                  <div className="meeting-action-menu" role="group"
                       aria-label={t('library.actions.aria', { titre: title })}
                       onClick={(event) => event.stopPropagation()}>
                    {projects.length > 0 && (
                      <label>
                        <span>{t('library.menu.projectLabel')}</span>
                        <select className="mini-select" aria-label={t('library.menu.classify.aria', { titre: title })}
                                value={meeting.retenu.project_id ?? ''}
                                onChange={(event) => move(meeting.cle, event.target.value, portee)}>
                          {projects.filter((candidate) => candidate.id).map((candidate) => (
                            <option key={candidate.id!} value={candidate.id!}>{libelleProjet(candidate.name, t, candidate.kind)}</option>
                          ))}
                        </select>
                      </label>
                    )}
                    <button type="button" className="menu-danger"
                            onClick={() => remove(meeting.cle, title, portee)}>
                      <Icon name="archive" /> {t('library.menu.archive')}
                    </button>
                  </div>
                )}
              </div>}
            </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
