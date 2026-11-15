import { useEffect, useMemo, useRef, useState } from 'react'
import { phraseDErreur } from '../lib/erreurs'
import type { AppConfig, ListeTraitements, Project } from '../api'
import { AnnulationError, DoublonError, EnvoiRefuseError, ModeleGratuitError, annulerTraitement, downloadUrl, getReglagesTraitement, setReglagesTraitement, streamJob, submitTranscribe } from '../api'
import { CODE_ANNULE } from '../lib/traitements'
import { libelleProjet } from '../lib/libelleProjet'
import { documentBody, isAtBottom, validateMediaFile } from '../lib/transcription'
import type { MediaProblem } from '../lib/transcription'
import { Icon } from './Icon'
import { RangerDans } from './RangerDans'
import { SelecteurFournisseur } from './SelecteurFournisseur'
import { Button } from '../design/primitives'
import { FOURNISSEUR_LOCAL_DE_REPLI, LIBELLES } from '../lib/fournisseurs'
import { fournisseurRetenuIndisponible, reglagesApplicables, sensibiliteApplicable, sensibiliteRetenable } from '../lib/reglagesTraitement'
import { MarkdownView } from './MarkdownView'
import { useCloudUltra } from '../lib/cloudUltra'
import { useLang } from '../lib/langContext'
import { useConfirmer } from '../design/Confirmation'
import { traduireEtape } from '../lib/i18n'
import type { EtapeJob, Lang, Traduire } from '../lib/i18n'
import type { MessageKey } from '../lib/messages'
import { cleSelonFaits, sensitivityLabelKey } from '../lib/messages'

type Props = {
  config: AppConfig
  onCompleted: () => void
  /** Les projets du compte : « Ranger dans » n'y propose jamais un projet reçu en lecture seule. */
  projects?: Project[]
  destinationProjectId?: string
  onChooseDestination?: (id: string) => void
  onCreateDestination?: (nom: string) => Promise<Project>
  /** Vrai quand la destination vient de la mémoire (« dernier projet utilisé »). */
  destinationSuggested?: boolean
  onViewMeeting?: (documentName: string, projectId: string) => void
  /** Après un envoi accepté : le parent relève la liste des traitements tout de suite. */
  onSubmitted?: (jobId: string) => void
  /** « Suivre » : ouvre le panneau des traitements. */
  onSuivre?: () => void
  /** Les traitements du compte, pour dire « en attente » quand le créneau est occupé. */
  traitements?: ListeTraitements | null
}

type JobResult = { name: string; content: string; destinationProjectId: string }
type TimelineStatus = 'pending' | 'current' | 'done' | 'skipped' | 'error'

type TimelineItem = {
  id: 'prepare' | 'stt' | 'diarize' | 'profile' | 'report' | 'archive'
  label: string
  detail: string
  start: number
  end: number
  enabled: boolean
}

/** La VALEUR envoyée au backend est fixe ; seul son libellé est traduit. */
const LANGUAGE_OPTIONS: { value: string; key: MessageKey }[] = [
  { value: '', key: 'spoken.auto' },
  { value: 'fr', key: 'spoken.fr' },
  { value: 'en', key: 'spoken.en' },
  { value: 'es', key: 'spoken.es' },
  { value: 'de', key: 'spoken.de' },
  { value: 'it', key: 'spoken.it' },
  { value: '__other__', key: 'spoken.other' },
]

function formatFileSize(t: Traduire, size: number): string {
  if (size >= 1_000_000_000) return t('size.gb', { valeur: (size / 1_000_000_000).toFixed(1) })
  if (size >= 1_000_000) return t('size.mb', { valeur: (size / 1_000_000).toFixed(1) })
  return t('size.kb', { valeur: Math.max(1, Math.round(size / 1_000)) })
}

/**
 * Rendu d'un VERDICT de validation. Le `switch` est exhaustif : ajouter un
 * motif dans `MediaProblem` sans le traduire devient une erreur `tsc`.
 */
/**
 * La ligne d'état du traitement, gardée sous forme NON TRADUITE.
 *
 * Elle était stockée en `string`, traduite au moment où on la posait. Changer
 * de langue pendant un traitement laissait donc la ligne figée dans l'ancienne
 * — et à la fin, un « Terminé » d'une langue sous un titre de l'autre. On garde
 * la clé ou le code, et la traduction se fait au RENDU.
 */
type LigneEtat =
  | { source: 'ui'; cle: MessageKey }
  | { source: 'job'; etape: EtapeJob }

function rendreLigne(t: Traduire, lang: Lang, ligne: LigneEtat | null): string {
  if (!ligne) return ''
  return ligne.source === 'ui' ? t(ligne.cle) : traduireEtape(lang, ligne.etape)
}

function messageMedia(t: Traduire, probleme: MediaProblem): string {
  switch (probleme.code) {
    case 'badExtension': return t('media.badExtension', { extension: probleme.extension })
    case 'noExtension': return t('media.noExtension')
    case 'empty': return t('media.empty')
    case 'tooLarge': return t('media.tooLarge', { limite: probleme.limite })
  }
}

/**
 * Le déroulé ne décrit RIEN par lui-même : les deux étapes qui dépendent du
 * mode reçoivent leur description déjà dérivée de l'état, calculée une seule
 * fois dans le composant. Deux endroits qui décrivent le même comportement
 * finissent par diverger.
 */
function timelineItems(
  t: Traduire,
  o: {
    sttDetail: string
    diarizeDetail: string
    llmNom: string
    diarize: boolean
    profile: boolean
    reportOn: boolean
  },
): TimelineItem[] {
  return [
    { id: 'prepare', label: t('timeline.prepare'), detail: t('timeline.prepare.detail'), start: 0, end: 0.10, enabled: true },
    { id: 'stt', label: t('timeline.stt'), detail: o.sttDetail, start: 0.10, end: 0.50, enabled: true },
    { id: 'diarize', label: t('timeline.diarize'), detail: o.diarizeDetail, start: 0.50, end: 0.65, enabled: o.diarize },
    { id: 'profile', label: t('timeline.profile'), detail: t('transcribe.profile.detail', { llm: o.llmNom }), start: 0.65, end: 0.75, enabled: o.profile },
    { id: 'report', label: t('timeline.report'), detail: t('transcribe.report.detail', { llm: o.llmNom }), start: 0.75, end: 0.95, enabled: o.reportOn },
    { id: 'archive', label: t('timeline.archive'), detail: t('timeline.archive.detail'), start: 0.95, end: 1, enabled: true },
  ]
}

function timelineStatus(
  item: TimelineItem,
  progress: number,
  started: boolean,
  complete: boolean,
  failed: boolean,
): TimelineStatus {
  if (!item.enabled) return 'skipped'
  if (complete) return 'done'
  if (!started) return 'pending'
  if (progress >= item.end) return 'done'
  if (progress >= item.start || item.id === 'prepare') return failed ? 'error' : 'current'
  return 'pending'
}

const FOCALISABLES = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'

export function TranscribePanel({
  config,
  onCompleted,
  projects = [],
  destinationProjectId = config.default_project_id,
  onChooseDestination = () => {},
  onCreateDestination,
  destinationSuggested = false,
  onSubmitted,
  onSuivre,
  traitements = null,
  onViewMeeting,
}: Props) {
  const { t, lang } = useLang()
  const confirmer = useConfirmer()
  const [file, setFile] = useState<File | null>(null)
  const [fileProblem, setFileProblem] = useState<MediaProblem | null>(null)
  // Le fournisseur de départ vient du serveur ; ce que le COMPTE a retenu s'y applique à l'ouverture (jamais le navigateur).
  const [llmProvider, setLlmProvider] = useState(config.default_llm_provider || FOURNISSEUR_LOCAL_DE_REPLI)
  // Le moteur STT n'est pas persisté : il se déduit du fournisseur, comme
  // `choisirFournisseur` le fait au choix. Sans cela, le fournisseur en ligne affiché
  // ne suivrait pas la transcription réellement employée.
  const [engine, setEngine] = useState(() => (
    (llmProvider === 'mistral' || llmProvider === 'cloud') && config.engines.includes('mistral')
      ? 'mistral'
      : config.default_engine
  ))
  // Ce que la PERSONNE a déjà réglé : les réglages du compte, qui arrivent après l'ouverture, ne l'écrasent pas.
  const touche = useRef({ fournisseur: false, langue: false, resultats: false, sensibilite: false })
  // Le fournisseur que le compte a retenu mais qui n'est pas configuré : dit en une ligne tant que la personne n'a rien choisi.
  const [retenuIndisponible, setRetenuIndisponible] = useState('')
  // La sensibilité retenue par le compte qui s'applique (plus stricte que le défaut) : dite en une ligne tant que la personne n'a pas choisi.
  const [sensibiliteRetenue, setSensibiliteRetenue] = useState('')
  const [languageChoice, setLanguageChoice] = useState('')
  const [language, setLanguage] = useState('')
  const [timestamps, setTimestamps] = useState(true)
  const [diarize, setDiarize] = useState(config.diarization_available)
  // L'INTENTION de l'utilisateur, pas l'état affiché. Les deux étaient
  // confondus, et initialisés sur `llm_available` — un OU sur tous les moteurs.
  // Sur une machine où Mistral est configuré mais le moteur local éteint, les
  // cases arrivaient donc COCHÉES, devenaient DÉSACTIVÉES faute de fournisseur
  // prêt, et la requête partait quand même avec `profile: true`. L'utilisateur
  // ne pouvait ni les décocher ni les honorer.
  //
  // L'intention est conservée pour qu'un retour vers un fournisseur disponible
  // la restitue, au lieu de punir un aller-retour.
  const [profileVoulu, setProfile] = useState(true)
  const [reportVoulu, setReportOn] = useState(true)
  // Sensibilité du document, choisie par l'utilisateur. Vocabulaire et défaut
  // PILOTÉS par le backend. Un document ultra-confidentiel ne part vers aucun
  // fournisseur cloud, sauf si le compte l'autorise (panneau Confidentialité) :
  // sans ce réglage, l'écran ne le propose pas, et l'API le refuse (403).
  const sensitivityLevels = config.sensitivity_levels || ['standard']
  const [sensitivity, setSensitivity] = useState(
    config.default_sensitivity || 'standard',
  )
  const cloudUltra = useCloudUltra()
  const ultraCloudAutorise = sensitivity === 'ultra_confidential' && cloudUltra
  const ultra = sensitivity === 'ultra_confidential' && !cloudUltra

  // `running` : un traitement se déroule dans ce panneau (frise, progression). `envoi` : un envoi est en cours,
  // le formulaire est verrouillé le temps qu'il soit ACCEPTÉ ; ensuite il reste prêt pour un autre envoi.
  const [running, setRunning] = useState(false)
  const [envoi, setEnvoi] = useState(false)
  const [progress, setProgress] = useState(0)
  const [ligne, setLigne] = useState<LigneEtat | null>(null)
  const [error, setError] = useState('')
  // Un envoi REFUSÉ pendant qu'un autre traitement est suivi : l'alerte se dit sans toucher à la frise du traitement suivi.
  const [erreurEnvoi, setErreurEnvoi] = useState('')
  // Le traitement suivi a été ANNULÉ (avant d'avoir commencé) : la frise s'arrête, sans style d'erreur.
  const [annule, setAnnule] = useState(false)
  const [confirmation, setConfirmation] = useState<{ jobId: string; projet: string } | null>(null)
  // Le fichier est déjà en attente ou en cours pour ce compte (409) : nommé, sans texte brut.
  const [doublon, setDoublon] = useState<{ jobId: string; titre: string } | null>(null)
  // Un envoi refusé AVANT le démarrage (plus de place, nuage refusé, modèle gratuit...) : rien n'a démarré, ce n'est pas un traitement interrompu.
  const [refus, setRefus] = useState<{ titre?: string; texte: string } | null>(null)
  const [result, setResult] = useState<JobResult | null>(null)
  const [partial, setPartial] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [warnings, setWarnings] = useState<string[]>([])
  // Sur petit écran, les réglages s'ouvrent en fenêtre : dialogue modal seulement tant qu'il est ouvert (un dialogue modal
  // toujours présent désactiverait le menu des réglages du compte), focus piégé, Échap, retour du focus au bouton d'ouverture.
  const [reglagesOuverts, setReglagesOuverts] = useState(false)
  const declencheurReglages = useRef<HTMLButtonElement>(null)
  const panneauReglages = useRef<HTMLElement>(null)
  // Annuler le traitement suivi tant qu'il attend sa place : UNE confirmation ; le flux du traitement dit ensuite « annulé ».
  const [annulation, setAnnulation] = useState(false)
  const [annulationRefusee, setAnnulationRefusee] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const liveRef = useRef<HTMLPreElement>(null)
  const stick = useRef(true)
  const stopStream = useRef<(() => void) | null>(null)

  useEffect(() => () => stopStream.current?.(), [])

  useEffect(() => {
    if (reglagesOuverts) panneauReglages.current?.querySelector<HTMLElement>(FOCALISABLES)?.focus()
  }, [reglagesOuverts])
  const fermerReglages = () => {
    setReglagesOuverts(false)
    declencheurReglages.current?.focus()
  }
  const surClavierReglages = (e: React.KeyboardEvent) => {
    if (!reglagesOuverts) return
    if (e.key === 'Escape') { e.stopPropagation(); fermerReglages(); return }
    if (e.key !== 'Tab') return
    const elements = [...(panneauReglages.current?.querySelectorAll<HTMLElement>(FOCALISABLES) ?? [])].filter((x) => x.offsetParent !== null || x === document.activeElement)
    const liste = elements.length > 0 ? elements : [...(panneauReglages.current?.querySelectorAll<HTMLElement>(FOCALISABLES) ?? [])]
    if (liste.length === 0) return
    const premier = liste[0]
    const dernier = liste[liste.length - 1]
    if (e.shiftKey && document.activeElement === premier) { e.preventDefault(); dernier.focus() }
    else if (!e.shiftKey && document.activeElement === dernier) { e.preventDefault(); premier.focus() }
  }

  useEffect(() => {
    const el = liveRef.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [partial])

  const acceptedFormats = useMemo(
    () => config.supported_extensions.join(', '),
    [config.supported_extensions],
  )
  // Un seul choix pilote la transcription ET la synthèse : « tout en local »
  // ou « tout via Mistral Cloud ». `mixte` n'est pas proposé, mais reste
  // atteignable par les paramètres experts — et alors affiché explicitement,
  // pour qu'une configuration hybride ne soit jamais silencieuse.
  // Mistral est-il configuré ? Vient du registre comme les autres — la clé
  // héritée `mistral_api_key_configured` a été retirée avec Ollama,
  // parce qu'elle nommait deux moteurs dans une charge utile qui les décrit
  // tous. Défini AVANT `pretDe`, qui n'en dépend plus.
  const fournisseurCle = llmProvider === 'cloud' ? 'mistral' : llmProvider
  const pretDe = (fournisseur: string): boolean => {
    // La porte GLOBALE d'abord : fausse = aucun moteur, quel que soit le reste.
    if (config.llm_available === false) return false
    // `llm_ready` vient du registre : complet, donc une clé absente = « ce moteur
    // n'existe pas ici », pas « on ne sait pas ». Sans lui : permissif.
    if (config.llm_ready) return config.llm_ready[fournisseur] === true
    return true
  }
  // LOCAL vs CLOUD par CAPACITÉ (backend `local_providers`, dérivé de `est_local`),
  // JAMAIS « == mistral » : un moteur cloud ajouté (OpenAI) y entre sans toucher ce
  // fichier. Le mode PRODUIT suit le fournisseur de RAPPORT ; le moteur STT (whisper
  // local / Voxtral) reste un réglage expert, secondaire.
  const localProviders = config.local_providers || ['mlx']
  const cloudProviders = (config.llm_providers || [])
    .filter((p) => !localProviders.includes(p) && p !== 'cloud')
  const estCloud = (p: string) => cloudProviders.includes(p)
  const mode: 'local' | 'cloud' = estCloud(fournisseurCle) ? 'cloud' : 'local'

  // Le nom d'un fournisseur vient de la table unique des libellés ; un fournisseur inconnu garde son identifiant.
  const nomCloud = (p: string) => (LIBELLES[p] ? t(LIBELLES[p]) : p)
  // UN seul chemin pour choisir un fournisseur (clic dans le sélecteur, retour au local, réglages du compte) : le choix est noté comme
  // fait par la personne, la ligne « retenu non configuré » s'efface, et le moteur de transcription suit le fournisseur — Mistral
  // apporte Voxtral ; les autres fournisseurs en ligne n'ont pas de transcription, elle reste locale (whisper).
  const choisirFournisseur = (p: string) => {
    touche.current.fournisseur = true
    setRetenuIndisponible('')
    setLlmProvider(p)
    setEngine(p === 'mistral' ? 'mistral' : 'whisper')
  }

  // Passer en ultra-confidentiel ramène au local : rien ne doit rester réglé sur le cloud.
  useEffect(() => {
    // Le mode local est le modèle local du serveur (`default_llm_provider`), relu, pas figé.
    if (ultra && (mode === 'cloud' || engine !== 'whisper')) choisirFournisseur(config.default_llm_provider || FOURNISSEUR_LOCAL_DE_REPLI)
  }, [ultra])

  // Les réglages du COMPTE s'appliquent UNE fois, à l'ouverture, par le même chemin que le clic (le moteur de transcription suit le
  // fournisseur) ; ils n'écrasent jamais ce que la personne a déjà réglé, et lire ne revient jamais à écrire. Illisibles : les
  // valeurs de départ. Un fournisseur retenu devenu périmé ou non prêt retombe sur « Local » (`reglagesApplicables`).
  const ultraCourant = useRef(ultra)
  ultraCourant.current = ultra
  const cloudUltraCourant = useRef(cloudUltra)
  cloudUltraCourant.current = cloudUltra
  useEffect(() => {
    let courant = true
    getReglagesTraitement().then(({ reglages }) => {
      if (!courant) return
      const r = reglagesApplicables(reglages, config)
      if (!r) return
      // La sensibilité qui s'applique : la plus stricte du défaut de l'instance et de celle que le compte a retenue (jamais en dessous du
      // défaut) ; le compte n'est pas réécrit. Calculée AVANT le fournisseur : un ultra-confidentiel retenu garde le traitement local
      // d'emblée, sans qu'un fournisseur en ligne soit appliqué un instant (ce qui ouvrirait « En ligne » tout seul).
      let ultraEnCours = ultraCourant.current
      if (!touche.current.sensibilite) {
        const applique = sensibiliteApplicable(reglages?.sensibilite, config)
        setSensitivity(applique)
        setSensibiliteRetenue(sensibiliteRetenable(reglages?.sensibilite ?? '', config) ? applique : '')
        ultraEnCours = applique === 'ultra_confidential' && !cloudUltraCourant.current
      }
      if (!touche.current.fournisseur) {
        setRetenuIndisponible(fournisseurRetenuIndisponible(reglages, config) ?? '')
        const enLigne = cloudProviders.includes(r.fournisseur)
        // Un document ultra-confidentiel reste local : le fournisseur en ligne retenu ne s'applique pas.
        if (!(enLigne && ultraEnCours)) {
          setLlmProvider(r.fournisseur)
          setEngine(r.fournisseur === 'mistral' && config.engines.includes('mistral') ? 'mistral' : 'whisper')
        }
      }
      if (!touche.current.langue && r.langue) {
        setLanguageChoice(LANGUAGE_OPTIONS.some((o) => o.value === r.langue) ? r.langue : '__other__')
        setLanguage(r.langue)
      }
      if (!touche.current.resultats) {
        setReportOn(r.resultats.compte_rendu)
        setProfile(r.resultats.titre_resume)
        setDiarize(r.resultats.locuteurs && config.diarization_available)
      }
    }).catch(() => { /* illisibles : les valeurs de départ */ })
    return () => { courant = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- une seule lecture, à l'ouverture
  }, [])

  // « Retenir comme réglages par défaut » : le fournisseur, la langue (seulement si c'est un code valide), les résultats voulus et la
  // sensibilité SEULEMENT si elle est plus stricte que le défaut de l'instance (sinon la clé est absente). Seul geste de l'écran qui
  // écrit au compte.
  const [retenu, setRetenu] = useState<'' | 'ok' | 'echec'>('')
  async function retenir() {
    setRetenu('')
    const code = language.trim().toLowerCase()
    const langueValide = code.length <= 12 && /^[a-z]{2,3}(-[a-z0-9]{2,8})?$/.test(code) ? code : ''
    try {
      await setReglagesTraitement({
        fournisseur: fournisseurCle, langue: langueValide,
        resultats: { compte_rendu: reportVoulu, titre_resume: profileVoulu, locuteurs: diarize },
        ...(sensibiliteRetenable(sensitivity, config) ? { sensibilite: sensitivity } : {}),
      })
      setRetenu('ok')
    } catch {
      setRetenu('echec')
    }
  }
  const llmPret = pretDe(fournisseurCle)
  // Un nom de modèle est une DONNÉE : il ne se traduit pas, et doit être
  // identique dans les deux langues.
  const llmNom = config.llm_models?.[fournisseurCle]
    || (fournisseurCle === 'mistral'
      ? (config.mistral_chat_model || nomCloud('mistral'))
      : (config.llm_model || fournisseurCle))

  // L'état EFFECTIF : ce qui s'affiche et ce qui part dans la requête. Une
  // option dont le fournisseur n'est pas prêt est décochée, pas cochée-et-figée.
  // Tout le reste du composant lit ces deux valeurs, jamais l'intention.
  const profile = profileVoulu && llmPret
  // Sans horodatages, un CR n'aurait rien à citer : l'API le refuse, l'option
  // ne se propose donc pas.
  const reportOn = reportVoulu && llmPret && timestamps
  // Mistral garde son message propre — la cause y est précise et actionnable
  // (une clé absente du `.env`). Tout moteur LOCAL partage un libellé PARAMÉTRÉ
  // par son nom : `missingOllama` nommait un moteur en dur, et MLX n'avait donc
  // aucun message à afficher.
  const llmManquant = estCloud(fournisseurCle)
    ? t('transcribe.llm.missingCloud', { provider: nomCloud(fournisseurCle) })
    : t('transcribe.llm.missingLocal', { llm: llmNom })

  // Le nom du moteur de transcription RÉELLEMENT employé. Le panneau expert
  // affichait `whisper_model` en toutes circonstances : en mode cloud, on y
  // lisait le nom d'un modèle qui ne tournait pas.
  const sttNom = engine === 'mistral'
    ? (config.mistral_stt_model || 'voxtral-mini-latest')
    : (config.whisper_model || 'Whisper')
  // Le déroulé annonçait « Whisper Large v3 Turbo » EN DUR : la phrase serait
  // devenue fausse au premier changement de `WHISPER_MODEL`. On nomme le modèle
  // configuré, sans son préfixe d'organisation qui n'apprend rien ici.
  const sttCourt = sttNom.split('/').pop() || sttNom
  const sttDetail = engine === 'mistral'
    ? t('timeline.stt.cloud', { modele: sttCourt })
    : t('timeline.stt.local', { modele: sttCourt })

  // Qui fait quoi quand « Identifier les locuteurs » est coché. DEUX faits
  // indépendants : quel moteur segmente (paramètre `moteur`), et si les
  // empreintes sont extraites (HF_TOKEN). Un troisième moteur n'ajoutera donc
  // aucune clé de traduction. Le dire évite deux malentendus : croire que le
  // cloud produit des empreintes tout seul, et croire qu'en mode cloud rien ne
  // tourne en local.
  const diarisationMoteur = engine === 'mistral'
    ? t('diar.engine.cloud')
    : t('diar.engine.local')
  const diarisationPossible = config.diarization_available || engine === 'mistral'
  const diarisationDetail = !diarisationPossible
    ? t('diar.unavailable')
    : config.diarization_available
      ? t('diar.withPrints', { moteur: diarisationMoteur })
      : t('diar.withoutPrints', { moteur: diarisationMoteur })
  // Le « pourquoi » n'a de sens que là où les empreintes existent, et n'est
  // affiché que sur la case à cocher : le déroulé reste court, mais les deux
  // textes partagent LA MÊME partie factuelle.
  const diarisationPourquoi = config.diarization_available ? t('diar.withPrints.why') : ''

  const timeline = useMemo(
    () => timelineItems(t, { sttDetail, diarizeDetail: diarisationDetail, llmNom, diarize, profile, reportOn }),
    [t, sttDetail, diarisationDetail, llmNom, diarize, profile, reportOn],
  )
  // Le traitement suivi attend sa place (un autre calcul lourd occupe la machine) : il n'a pas commencé, donc ni
  // « envoi sécurisé » ni étape active à montrer. La liste partagée des traitements le dit.
  async function annulerLeSuivi() {
    if (!confirmation || annulation) return
    if (!await confirmer({ titre: t('traitements.cancel.titre'), phrase: t('traitements.cancel.confirm'), libelleAction: t('traitements.cancel.action'), libelleRefus: t('traitements.cancel.keep') })) return
    setAnnulation(true)
    setAnnulationRefusee('')
    try {
      await annulerTraitement(confirmation.jobId)
    } catch (caught) {
      setAnnulationRefusee(t(caught instanceof AnnulationError
        ? (caught.raison === 'impossible' ? 'traitements.cancel.impossible' : 'traitements.cancel.gone')
        : 'traitements.cancel.failed'))
    } finally {
      setAnnulation(false)
    }
  }
  const suiviEnAttente = Boolean(running && confirmation && traitements?.traitements.some(
    (x) => x.id === confirmation.jobId && (x.statut === 'pending' || x.en_attente)))
  const started = (running || Boolean(result) || Boolean(error) || progress > 0) && !annule && !suiviEnAttente
  const flowStep = running || result || error ? 3 : file ? 2 : 1

  function chooseFile(candidate: File | null) {
    if (!candidate) return
    const probleme = validateMediaFile(candidate, config.supported_extensions, config.max_upload_mb)
    setFileProblem(probleme)
    setResult(null)
    setError('')
    setErreurEnvoi('')
    setDoublon(null)
    setRefus(null)
    setAnnule(false)
    // Un traitement lancé est encore SUIVI ici : choisir un autre fichier ne remet pas sa frise à zéro.
    if (!stopStream.current) {
      setPartial('')
      setProgress(0)
      setLigne(null)
    }
    setFile(probleme ? null : candidate)
  }

  /** « Retirer ce fichier » : ne retire que le CHOIX. Le suivi du traitement déjà lancé et sa confirmation restent. */
  function retirerFichier() {
    if (!stopStream.current && !confirmation && !running) { clearFile(); return }
    setFile(null)
    setFileProblem(null)
    setError('')
    setErreurEnvoi('')
    setDoublon(null)
    setRefus(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  function clearFile() {
    stopStream.current?.()
    stopStream.current = null
    setRunning(false)
    setConfirmation(null)
    setFile(null)
    setFileProblem(null)
    setResult(null)
    setError('')
    setErreurEnvoi('')
    setAnnule(false)
    setDoublon(null)
    setRefus(null)
    setPartial('')
    setProgress(0)
    setLigne(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  async function run() {
    if (!file) return
    const runDestination = destinationProjectId
    // Un traitement déjà lancé reste SUIVI ici tant que le nouvel envoi n'est pas ACCEPTÉ : un refus (doublon, plus de
    // place, panne) ne doit ni couper sa frise ni effacer sa confirmation.
    const suivi = stopStream.current !== null
    setEnvoi(true)
    setError('')
    setErreurEnvoi('')
    setDoublon(null)
    setRefus(null)
    setAnnule(false)
    if (!suivi) {
      setRunning(true)
      setResult(null)
      setPartial('')
      setWarnings([])
      stick.current = true
      setProgress(0)
      setConfirmation(null)
      setLigne({ source: 'ui', cle: 'transcribe.upload' })
    }
    try {
      const jobId = await submitTranscribe(file, {
        engine,
        language,
        diarize,
        profile,
        report_on: reportOn,
        timestamps,
        destinationProjectId: runDestination,
        llm_provider: llmProvider,
        sensitivity,
      })
      // L'envoi est ACCEPTÉ : le traitement précédent encore suivi ici est détaché (il continue côté serveur et reste
      // visible dans le panneau des traitements) ; la frise de ce panneau suit le nouvel envoi.
      stopStream.current?.()
      stopStream.current = null
      setRunning(true)
      setResult(null)
      setPartial('')
      setWarnings([])
      stick.current = true
      setProgress(0)
      setLigne({ source: 'ui', cle: 'transcribe.upload' })
      // On le dit tout de suite (où la réunion est rangée) et on offre de la suivre.
      // Le formulaire reste prêt : l'écran n'attend pas la fin du traitement.
      setEnvoi(false)
      const rangeDans = projects.find((p) => p.id === runDestination)
      setConfirmation({
        jobId,
        projet: libelleProjet(rangeDans?.name ?? config.default_project, t, rangeDans?.kind),
      })
      // Le choix est VIDÉ : le bouton « Transcrire un autre enregistrement » ne doit jamais renvoyer le même fichier
      //. Contrairement à `clearFile`, le suivi du traitement lancé n'est PAS coupé.
      setFile(null)
      setFileProblem(null)
      if (inputRef.current) inputRef.current.value = ''
      onSubmitted?.(jobId)
      stopStream.current = streamJob(jobId, {
        onProgress: (p, _m, etape) => { setProgress(p); setLigne({ source: 'job', etape }) },
        onPartial: (text) => setPartial((previous) => (previous ? `${previous} ${text}` : text)),
        // Une etape DERIVEE a echoue sans perdre le Markdown : on l'affiche
        // plutot que de laisser croire a un traitement complet.
        onWarning: (text) => setWarnings((previous) => [...previous, text]),
        onDone: (completed) => {
          setResult({ ...completed, destinationProjectId: runDestination })
          setRunning(false)
          setProgress(1)
          setLigne({ source: 'ui', cle: 'transcribe.result.ready' })
          setPartial('')
          stopStream.current = null
          onCompleted()
        },
        onError: (streamError, etape) => {
          setRunning(false)
          stopStream.current = null
          // Annulé avant d'avoir commencé : un choix, pas une panne. La frise s'arrête, sans alerte.
          // La confirmation « Rangé dans… » ne dit plus vrai : rien n'est enregistré, l'écran redevient prêt à l'envoi.
          if (etape?.code === CODE_ANNULE) { setAnnule(true); setConfirmation(null); return }
          // Le code de l'échec se traduit. Exception nommée : un serveur plus ancien n'envoie pas de code, et le texte du serveur
          // reste alors affiché (un test l'exige) ; elle n'a pas de clé de catalogue, donc pas de ligne dans la garde du catalogue.
          setError(etape?.code ? traduireEtape(lang, etape) : streamError)
        },
      })
    } catch (caught) {
      // Un modèle gratuit refuse ce document : SA phrase (traduite), jamais le texte brut ni celle de l'ultra-confidentiel.
      const dit = caught instanceof ModeleGratuitError ? t('job.step.error.freeModelRestricted') : phraseDErreur(t, lang, caught, t('repli.envoi'))
      if (caught instanceof DoublonError) setDoublon({ jobId: caught.jobId, titre: caught.titre })
      else if (caught instanceof ModeleGratuitError) setRefus({ titre: t('envoi.refus.titre'), texte: dit })
      else if (caught instanceof EnvoiRefuseError) {
        // Un code connu donne la phrase du catalogue (dans la langue choisie) ; sans code connu, la phrase générale : jamais celle du serveur.
        const phrase = phraseDErreur(t, lang, caught, '')
        setRefus({ texte: phrase ? t('envoi.refus.detail', { detail: phrase }) : t('envoi.refus.general') })
      }
      else if (suivi) setErreurEnvoi(dit)
      else setError(dit)
      if (!suivi) setRunning(false)
      setEnvoi(false)
    }
  }

  return (
    <section className="transcribe-studio" aria-labelledby="transcribe-title">
      <header className="transcribe-hero">
        <div>
          <span className="eyebrow">{t('transcribe.eyebrow')}</span>
          <h1 id="transcribe-title">{t('transcribe.title')}</h1>
          <p>{t('transcribe.subtitle')}</p>
        </div>
        <ol className="flow-steps" aria-label={t('transcribe.flow.label')}>
          {(['transcribe.flow.file', 'transcribe.flow.options', 'transcribe.flow.processing'] as const).map((key, index) => {
            const number = index + 1
            const state = number < flowStep ? 'done' : number === flowStep ? 'current' : 'pending'
            return (
              <li key={key} className={state} aria-current={state === 'current' ? 'step' : undefined}>
                <span>{state === 'done' ? <Icon name="check" /> : number}</span>
                {t(key)}
              </li>
            )
          })}
        </ol>
      </header>

      <div className="transcribe-layout">
        {/* Zone de gauche : les réglages du traitement. Sur petit écran, ils s'ouvrent en fenêtre (dialogue modal, focus piégé, Échap). */}
        <section
          ref={panneauReglages}
          className={`studio-panel reglages-panel ${reglagesOuverts ? 'ouvert' : ''}`}
          role={reglagesOuverts ? 'dialog' : undefined}
          aria-modal={reglagesOuverts ? true : undefined}
          aria-labelledby="reglages-title"
          onKeyDown={surClavierReglages}
        >
          <div className="studio-panel-heading">
            <div><h2 id="reglages-title">{t('reglages.titre')}</h2><p>{t('transcribe.options.subtitle')}</p></div>
            <button type="button" className="icon-btn reglages-fermer" onClick={fermerReglages} aria-label={t('reglages.fermer')}>
              <Icon name="close" />
            </button>
          </div>

          <div className="stt-engine-section">
            {/* Le sélecteur partagé : « Local » d'abord, puis « En ligne » repliable avec chaque fournisseur et son état. */}
            <SelecteurFournisseur config={config} valeur={fournisseurCle} onChange={choisirFournisseur}
                                  legende={t('transcribe.mode.label')} nom="fournisseur-transcrire"
                                  ultra={sensitivity === 'ultra_confidential'} cloudUltra={cloudUltra} desactive={envoi} />
          </div>

          {/* Ce que le choix veut dire : où partent l'audio et le texte. */}
          {mode === 'local' && <p className="muted small">{t('transcribe.mode.localDetail')}</p>}
          {mode === 'cloud' && engine === 'mistral' && <p className="muted small">{t('transcribe.mode.cloudDetail', { fournisseur: nomCloud(fournisseurCle) })}</p>}
          {/* Un fournisseur en ligne qui ne transcrit pas (tout sauf Mistral) : seul le texte part chez lui. */}
          {mode === 'cloud' && engine !== 'mistral' && (
            <p className="muted small">{t('fournisseur.texteSeulEnLigne')}</p>
          )}
          {retenuIndisponible && (
            <p className="muted small reglages-indispo" role="status">
              {t('reglages.retenuIndisponible', { fournisseur: LIBELLES[retenuIndisponible] ? t(LIBELLES[retenuIndisponible]) : retenuIndisponible })}
            </p>
          )}
          <label className="field">{t('spoken.label')}
            <select className="option-select" aria-label={t('spoken.label')} value={languageChoice}
                    onChange={(event) => {
                      const value = event.target.value
                      touche.current.langue = true
                      setLanguageChoice(value)
                      setLanguage(value === '__other__' ? '' : value)
                    }} disabled={envoi}>
              {LANGUAGE_OPTIONS.map((candidate) => (
                <option key={candidate.value} value={candidate.value}>{t(candidate.key)}</option>
              ))}
            </select>
          </label>
          {languageChoice === '__other__' && (
            <label className="field custom-language">{t('spoken.codeLabel')}
              <input type="text" value={language} placeholder={t('spoken.codePlaceholder')} maxLength={12}
                     autoCapitalize="none" spellCheck={false} disabled={envoi}
                     onChange={(event) => { touche.current.langue = true; setLanguage(event.target.value.trim().toLowerCase()) }} />
            </label>
          )}

          {/* Sensibilité du document. Petit sélecteur, sous-titré pour DIRE que
              c'est la nature du document, pas le fournisseur d'IA ; en
              ultra-confidentiel, il garde tout en local. */}
          <label className="field">{t('sensitivity.label')}
            <select className="option-select" aria-label={t('sensitivity.label')}
                    value={sensitivity} disabled={envoi}
                    onChange={(event) => { touche.current.sensibilite = true; setSensitivity(event.target.value) }}>
              {sensitivityLevels.map((niveau) => (
                <option key={niveau} value={niveau}>{t(sensitivityLabelKey(niveau))}</option>
              ))}
            </select>
            <small className="field-note">{t(ultra ? cleSelonFaits('sensitivity.hint.ultra', config.faits === true)
              : ultraCloudAutorise ? 'sensitivity.hint.ultraCloud' : 'sensitivity.hint')}</small>
            {/* Une sensibilité retenue par le compte qui s'applique : dite ici, tant que la personne ne choisit pas autre chose. */}
            {sensibiliteRetenue && sensitivity === sensibiliteRetenue && (
              <small className="field-note" role="status">
                {t('reglages.sensibiliteRetenue', { niveau: t(sensitivityLabelKey(sensibiliteRetenue)) })}
              </small>
            )}
          </label>

          <div className="enhancement-grid">
            <label className={`option-tile ${diarize && diarisationPossible ? 'on' : ''} ${diarisationPossible ? '' : 'disabled'}`}>
              <input type="checkbox" checked={diarize && diarisationPossible}
                     disabled={envoi || !diarisationPossible}
                     onChange={(event) => { touche.current.resultats = true; setDiarize(event.target.checked) }} />
              <span className="option-icon"><Icon name="users" /></span>
              <span>
                <strong>{t('transcribe.diarize.label')}</strong>
                <small>
                  {/* Voxtral rend des ÉTIQUETTES de locuteurs, pas de
                      vecteurs : les empreintes sont un sous-produit de
                      pyannote. Le registre « Voix » reste donc vide pour une
                      réunion transcrite en cloud — à dire, pas à taire. */}
                  {diarisationDetail}{diarisationPourquoi}
                </small>
              </span>
            </label>
            <label className={`option-tile ${profile ? 'on llm' : ''} ${llmPret ? '' : 'disabled'}`}>
              <input type="checkbox" checked={profile} disabled={envoi || !llmPret}
                     onChange={(event) => { touche.current.resultats = true; setProfile(event.target.checked) }} />
              <span className="option-icon"><Icon name="sparkles" /></span>
              <span><strong>{t('transcribe.profile.label')}</strong><small>{llmPret ? t('transcribe.profile.carte') : llmManquant}</small></span>
            </label>
            <label className={`option-tile ${reportOn ? 'on llm' : ''} ${llmPret && timestamps ? '' : 'disabled'}`}>
              <input type="checkbox" checked={reportOn} disabled={envoi || !llmPret || !timestamps}
                     onChange={(event) => { touche.current.resultats = true; setReportOn(event.target.checked) }} />
              <span className="option-icon"><Icon name="report" /></span>
              <span><strong>{t('transcribe.report.label')}</strong><small>{!llmPret ? llmManquant
                : timestamps ? t('transcribe.report.detail', { llm: llmNom })
                  : t('transcribe.report.needsTimestamps')}</small></span>
            </label>
          </div>

          <details className="expert-options">
            <summary><Icon name="settings" /> {t('transcribe.expert.title')}</summary>
            <div className="expert-content">
              <label><input type="checkbox" checked={timestamps} disabled={envoi}
                            onChange={(event) => setTimestamps(event.target.checked)} /> {t('transcribe.expert.timestamps')}</label>
              {config.engines.length > 1 && (
                <label className="field">{t('transcribe.expert.engine')}
                  <select className="option-select" value={engine} disabled={envoi}
                          onChange={(event) => setEngine(event.target.value)}>
                    {config.engines.map((candidate) => (
                      <option key={candidate} value={candidate}
                              disabled={ultra && candidate !== 'whisper'}>{candidate}</option>
                    ))}
                  </select>
                </label>
              )}
              <dl className="model-facts">
                <div><dt>{t('transcribe.expert.stt')}</dt><dd>{sttNom}</dd></div>
                {profile && <div><dt>{t('transcribe.expert.llm')}</dt><dd>{llmNom}</dd></div>}
              </dl>
            </div>
          </details>

          <div className="reglages-defaut">
            <button type="button" className="btn sm" onClick={() => void retenir()} disabled={envoi}>
              {t('reglages.retenir')}
            </button>
            {retenu === 'ok' && <span role="status" className="muted small">{t('reglages.retenus')}</span>}
            {retenu === 'echec' && <span role="alert" className="error small">{t('reglages.echec')}</span>}
          </div>
        </section>

        {/* Zone de droite : le dépôt, « Ranger dans » et « Lancer » sur une même ligne, puis le suivi. */}
        <div className="transcribe-work-column">
          <section className="studio-panel file-step" aria-labelledby="file-step-title">
            <div className="studio-panel-heading">
              <div><h2 id="file-step-title">{t('transcribe.file.title')}</h2><p>{t('transcribe.file.subtitle')}</p></div>
              {/* Petit écran : le bouton qui ouvre les réglages en fenêtre (caché en bureau, où le panneau est déjà à gauche). */}
              <button type="button" ref={declencheurReglages} className="btn reglages-ouvrir" aria-expanded={reglagesOuverts}
                      onClick={() => setReglagesOuverts(true)}>
                <Icon name="settings" /> {t('reglages.titre')}
              </button>
            </div>

            <div
              className={`dropzone studio-dropzone ${dragOver ? 'over' : ''} ${file ? 'has-file' : ''}`}
              role="button"
              tabIndex={envoi ? -1 : 0}
              aria-disabled={envoi}
              aria-label={file ? t('transcribe.file.change', { nom: file.name }) : t('transcribe.file.choose')}
              onClick={() => { if (!envoi) inputRef.current?.click() }}
              onKeyDown={(event) => {
                if (!envoi && (event.key === 'Enter' || event.key === ' ')) {
                  event.preventDefault(); inputRef.current?.click()
                }
              }}
              onDragOver={(event) => { if (!envoi) { event.preventDefault(); setDragOver(true) } }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(event) => {
                event.preventDefault(); setDragOver(false)
                if (!envoi) chooseFile(event.dataTransfer.files[0] ?? null)
              }}
            >
              <input
                ref={inputRef}
                type="file"
                accept={config.supported_extensions.join(',')}
                hidden
                disabled={envoi}
                onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
              />
              <div className="drop-icon"><Icon name={file ? 'check' : 'upload'} /></div>
              {file ? (
                <>
                  <strong>{file.name}</strong>
                  <span className="file-facts">{t('transcribe.file.ready', { taille: formatFileSize(t, file.size) })}</span>
                  <span className="drop-action">{t('transcribe.file.replace')}</span>
                </>
              ) : (
                <>
                  <strong>{t('transcribe.file.drop')}</strong>
                  <span>{t('transcribe.file.browse')}</span>
                  <span className="drop-action">{t('transcribe.file.limit', { limite: config.max_upload_mb })}</span>
                </>
              )}
            </div>
            {file && !envoi && (
              <button type="button" className="text-action file-remove" onClick={retirerFichier}>
                <Icon name="close" /> {t('transcribe.file.remove')}
              </button>
            )}
            {fileProblem && (
              <p className="error file-validation" role="alert">
                <Icon name="error" /> {messageMedia(t, fileProblem)} {t('media.acceptedFormats', { formats: acceptedFormats })}
              </p>
            )}
            {/* La destination se choisit ICI, avant d'envoyer : elle ne change pas ce que la Bibliothèque consulte. */}
            <div className="ligne-envoi" data-ligne="envoi">
              {/* La barre collante recouvre la carte du fichier sur une fenêtre basse : elle redit quel fichier part. */}
              {file && <p className="envoi-fichier" title={file.name}><span>{file.name}</span></p>}
              <RangerDans projets={projects} destination={destinationProjectId}
                          surChoisir={onChooseDestination} desactive={envoi}
                          surCreer={onCreateDestination ?? (() => Promise.reject(new Error('no-create')))}
                          proposee={destinationSuggested} nomParDefaut={libelleProjet(config.default_project, t)} />
              {(file || confirmation) && (
                <button className="primary transcribe-submit" disabled={envoi}
                        onClick={() => {
                          // Sans fichier, après un envoi accepté : demander QUEL enregistrement, jamais renvoyer le même.
                          if (!file && confirmation) inputRef.current?.click()
                          else void run()
                        }}>
                  <Icon name={envoi ? 'clock' : 'upload'} />
                  {/* Pendant l'envoi : « Traitement en cours… » ; ensuite le formulaire est PRÊT et le bouton le dit
                      (un traitement tourne déjà, on peut en lancer un autre). */}
                  {envoi ? t('transcribe.submitting') : (running || (confirmation && !file)) ? t('transcribe.submitAnother') : t('transcribe.submit')}
                </button>
              )}
            </div>

            {/* Tant qu'un envoi accepté est confirmé, même fichier vidé : la confirmation et « Transcrire un autre enregistrement » vivent ici. */}
            {(file || confirmation) && (
              <>
                {confirmation && (
                  <div className="traitements-confirmation" role="status" aria-label={t('traitements.confirm.aria')}>
                    <Icon name="check" />
                    <span>
                      {t('traitements.confirm.saved', { projet: confirmation.projet })}
                      {traitements?.traitements.some((x) => x.id === confirmation.jobId && (x.statut === 'pending' || x.en_attente))
                        && <> {t('traitements.waiting.hint')}</>}
                    </span>
                    {onSuivre && <button type="button" className="btn sm" onClick={onSuivre}>{t('traitements.confirm.follow')}</button>}
                  </div>
                )}
                {warnings.length > 0 && (
                  <div className="error job-error" role="status" style={{ background: 'transparent' }}>
                    <Icon name="error" />
                    <span>
                      <strong>{t('transcribe.partialFailure')}</strong>
                      {warnings.map((w) => <span key={w}>{w}. </span>)}
                    </span>
                  </div>
                )}
                {doublon && (
                  <div className="error job-error" role="alert">
                    <Icon name="error" />
                    <span>
                      <strong>{t('transcribe.duplicate')}</strong> {t('transcribe.duplicate.existing', { titre: doublon.titre })}
                    </span>
                    {onSuivre && <button type="button" className="btn sm" onClick={onSuivre}>{t('traitements.confirm.follow')}</button>}
                  </div>
                )}
                {refus && (
                  <div className="error job-error" role="alert">
                    <Icon name="error" />
                    <span>{refus.titre && <strong>{refus.titre} </strong>}{refus.texte}</span>
                  </div>
                )}
                {error && <div className="error job-error" role="alert"><Icon name="error" /><span><strong>{t('transcribe.aborted')}</strong>{error}</span></div>}
                {erreurEnvoi && <div className="error job-error" role="alert"><Icon name="error" /><span><strong>{t('transcribe.aborted')}</strong>{erreurEnvoi}</span></div>}
              </>
            )}
          </section>

          <aside className="studio-panel process-panel" aria-labelledby="process-title">
            <div className="studio-panel-heading">
              <div><h2 id="process-title">{t('transcribe.process.title')}</h2><p>{annule ? t('traitements.cancelled.message')
                : suiviEnAttente ? t('traitements.waiting.hint')
                  : started ? rendreLigne(t, lang, ligne) || t('transcribe.process.init') : t('transcribe.process.idle')}</p></div>
            </div>
            {/* Tant qu'un traitement tourne ou attend sa place : on peut quitter la page, il continue côté serveur. */}
            {running && <p className="muted small">{t('transcribe.process.leave')}</p>}
            {suiviEnAttente && (
              <div className="suivi-annulation">
                <button type="button" className="btn sm" disabled={annulation} onClick={() => void annulerLeSuivi()}>
                  {t('traitements.cancel')}
                </button>
                {annulationRefusee && <span role="alert" className="error small">{annulationRefusee}</span>}
              </div>
            )}

            <div className="process-progress" aria-label={t('transcribe.process.progress', { pourcent: Math.round(progress * 100) })}>
              <div className="bar"><div className="fill" style={{ width: `${Math.round(progress * 100)}%` }} /></div>
              <strong>{Math.round(progress * 100)} %</strong>
            </div>

            <ol className="job-timeline">
              {timeline.map((item) => {
                const status = timelineStatus(item, progress, started, Boolean(result), Boolean(error))
                return (
                  <li key={item.id} className={status} aria-current={status === 'current' || status === 'error' ? 'step' : undefined}>
                    <span className="timeline-marker">
                      {status === 'done' ? <Icon name="check" /> : status === 'error' ? <Icon name="error" /> : null}
                    </span>
                    <span><strong>{item.label}</strong><small>{status === 'skipped' ? t('timeline.skipped') : item.detail}</small></span>
                  </li>
                )
              })}
            </ol>

            <div className={`live-result ${result ? 'ready' : partial ? 'streaming' : ''}`}>
              {result ? (
                <>
                  <div className="result-heading">
                    <span className="result-check"><Icon name="check" /></span>
                    <span><small>{t('transcribe.result.ready')}</small><strong>{result.name}</strong></span>
                  </div>
                  <div className="result-actions">
                    {onViewMeeting && (
                      <Button variant="primary" onClick={() => onViewMeeting(result.name, result.destinationProjectId)}>
                        {t('transcribe.result.view')}
                      </Button>
                    )}
                    {/* Le document vient d'être écrit DANS cette destination :
                        c'est sa portée, et non celle du projet affiché. */}
                    <a className="btn" href={downloadUrl(result.name, result.destinationProjectId)}>{t('transcribe.result.download')}</a>
                    <Button variant="ghost" onClick={clearFile}>{t('transcribe.result.again')}</Button>
                  </div>
                  <div className="result-preview"><MarkdownView content={documentBody(result.content)} /></div>
                </>
              ) : partial ? (
                <>
                  <p className="live-label"><Icon name="feather" /> {t('transcribe.preview.label')}</p>
                  <pre ref={liveRef} className="preview live"
                       onScroll={(event) => { stick.current = isAtBottom(event.currentTarget) }}>
                    {partial}<span className="caret" />
                  </pre>
                </>
              ) : (
                <div className="process-empty">
                  <Icon name="feather" />
                  <strong>{t('transcribe.preview.empty')}</strong>
                  <span>{t('transcribe.preview.truth')}</span>
                </div>
              )}
            </div>
          </aside>

          {/* Le compagnon s'explique ici, sans être piloté depuis la page. */}
          <section className="studio-panel companion-note" aria-labelledby="companion-title">
            <h2 id="companion-title">{t('transcribe.companion.title')}</h2>
            <p>{t('transcribe.companion.body')}</p>
            <p>{t('transcribe.companion.headset')}</p>
            <p className="muted small">{t('transcribe.companion.install', { readme: 'compagnon/README.md' })}</p>
          </section>
        </div>
      </div>
    </section>
  )
}
