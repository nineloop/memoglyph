import { useEffect, useMemo, useRef, useState } from 'react'
import type { AppConfig, ListeTraitements, Project } from '../api'
import { DoublonError, downloadUrl, streamJob, submitTranscribe } from '../api'
import { CODE_ANNULE } from '../lib/traitements'
import { libelleProjet } from '../lib/libelleProjet'
import { documentBody, isAtBottom, validateMediaFile } from '../lib/transcription'
import type { MediaProblem } from '../lib/transcription'
import { Icon } from './Icon'
import { RangerDans } from './RangerDans'
import { Button } from '../design/primitives'
import { getLlmProvider, setLlmProvider as setLlmProvider_store } from '../lib/llmProvider'
import { MarkdownView } from './MarkdownView'
import { useCloudUltra } from '../lib/cloudUltra'
import { useLang } from '../lib/langContext'
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
  const [file, setFile] = useState<File | null>(null)
  const [fileProblem, setFileProblem] = useState<MediaProblem | null>(null)
  const [llmProvider, setLlmProviderState] = useState(() => getLlmProvider(config.default_llm_provider || 'mlx'))
  // Le moteur STT n'est pas persisté : il se déduit du fournisseur MÉMORISÉ,
  // comme `setCloudProvider` le fait au clic. Il repartait sur whisper au
  // rechargement, alors que la tuile cloud, qui suit le fournisseur, restait
  // allumée : l'écran annonçait Voxtral et l'audio était transcrit en local.
  const [engine, setEngine] = useState(() => (
    (llmProvider === 'mistral' || llmProvider === 'cloud') && config.engines.includes('mistral')
      ? 'mistral'
      : config.default_engine
  ))
  // Persiste le choix pour que le CR à la demande et l'Assistant le voient aussi.
  const setLlmProvider = (p: string) => { setLlmProviderState(p); setLlmProvider_store(p) }
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
  // Le traitement suivi a été ANNULÉ (avant d'avoir commencé) : la frise s'arrête, sans style d'erreur (chantier 005).
  const [annule, setAnnule] = useState(false)
  const [confirmation, setConfirmation] = useState<{ jobId: string; projet: string } | null>(null)
  // Le fichier est déjà en attente ou en cours pour ce compte (409) : nommé, sans texte brut.
  const [doublon, setDoublon] = useState<{ jobId: string; titre: string } | null>(null)
  const [result, setResult] = useState<JobResult | null>(null)
  const [partial, setPartial] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [warnings, setWarnings] = useState<string[]>([])
  const inputRef = useRef<HTMLInputElement>(null)
  const liveRef = useRef<HTMLPreElement>(null)
  const stick = useRef(true)
  const stopStream = useRef<(() => void) | null>(null)

  useEffect(() => () => stopStream.current?.(), [])

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
  const readyCloud = cloudProviders.filter(pretDe)
  const cloudPret = readyCloud.length > 0
  const estCloud = (p: string) => cloudProviders.includes(p)
  const mode: 'local' | 'cloud' = estCloud(fournisseurCle) ? 'cloud' : 'local'

  // Nom d'affichage court (donnée, pas logique) ; repli sur l'id si inconnu.
  const NOMS_CLOUD: Record<string, string> = {
    mistral: 'Mistral', openai: 'OpenAI', anthropic: 'Anthropic (Claude)', compatible: t('app.provider.compatible'),
  }
  const nomCloud = (p: string) => NOMS_CLOUD[p] || p
  // Un serveur hébergé par l'utilisateur hors de cette machine : « cloud privé ».
  const prives = config.private_cloud_providers || []
  // Plusieurs moteurs locaux prêts (MLX, un serveur compatible sur la machine) : le
  // mode local propose aussi de choisir.
  const readyLocal = localProviders.filter((p) => (config.llm_providers || []).includes(p) && pretDe(p))
  // Choisir un fournisseur cloud règle le RAPPORT et couple le STT : Mistral apporte
  // Voxtral (engine=mistral) ; les autres cloud n'ont pas de STT → chemin local
  // (whisper). Préserve l'UX Mistral existante.
  const setCloudProvider = (p: string) => {
    setLlmProvider(p)
    setEngine(p === 'mistral' ? 'mistral' : 'whisper')
  }
  const setMode = (choix: 'local' | 'cloud') => {
    if (choix === 'local') {
      setEngine('whisper')
      // Le mode local est MLX (`default_llm_provider`) — relu, pas figé.
      setLlmProvider(config.default_llm_provider || 'mlx')
      return
    }
    // Cloud : garder le fournisseur cloud courant s'il est prêt, sinon le 1er prêt
    // (préférence Mistral pour la compatibilité).
    const courant = estCloud(fournisseurCle) && pretDe(fournisseurCle) ? fournisseurCle : null
    const choisi = courant || (readyCloud.includes('mistral') ? 'mistral' : readyCloud[0])
    if (choisi) setCloudProvider(choisi)
  }

  // UN CHOIX PERSISTÉ PEUT ÊTRE PÉRIMÉ, et c'est le risque de la sortie
  // d'Ollama : `memoglyph.llmProvider = "ollama"` survit au déploiement, le
  // type fermé du backend refuse la valeur, et l'utilisateur reçoit une erreur
  // dure au lieu d'un produit.
  //
  // On retombe sur le défaut annoncé par l'API, et on RÉÉCRIT le stockage —
  // sans la réécriture, la correction serait à refaire à chaque chargement.
  //
  // Pas d'alias `ollama → mlx` côté backend : il ferait tourner un autre moteur
  // que celui demandé, en silence. C'est exactement ce que le type fermé
  // interdit.
  useEffect(() => {
    const connus = config.llm_providers
    if (!connus?.length || connus.includes(fournisseurCle)) return
    setLlmProvider(config.default_llm_provider || 'mlx')
  }, [config.llm_providers, config.default_llm_provider, fournisseurCle])
  // Passer en ultra-confidentiel ramène au local : rien ne doit rester réglé sur le cloud.
  useEffect(() => {
    if (ultra && (mode === 'cloud' || engine !== 'whisper')) setMode('local')
  }, [ultra])
  const llmPret = pretDe(fournisseurCle)
  // Un nom de modèle est une DONNÉE : il ne se traduit pas, et doit être
  // identique dans les deux langues.
  const llmNom = config.llm_models?.[fournisseurCle]
    || (fournisseurCle === 'mistral'
      ? (config.mistral_chat_model || 'Mistral')
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
  const llmManquant = fournisseurCle === 'mistral'
    ? t('transcribe.llm.missingMistral')
    : estCloud(fournisseurCle)
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
  // « envoi sécurisé » ni étape active à montrer (chantier 005). La liste partagée des traitements le dit.
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
    setPartial('')
    setProgress(0)
    setLigne(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  async function run() {
    if (!file) return
    const runDestination = destinationProjectId
    // Un traitement déjà lancé reste SUIVI ici tant que le nouvel envoi n'est pas ACCEPTÉ : un refus (doublon, plus de
    // place, panne) ne doit ni couper sa frise ni effacer sa confirmation (chantier 005).
    const suivi = stopStream.current !== null
    setEnvoi(true)
    setError('')
    setErreurEnvoi('')
    setDoublon(null)
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
      // (chantier 005). Contrairement à `clearFile`, le suivi du traitement lancé n'est PAS coupé.
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
          // Annulé avant d'avoir commencé : un choix, pas une panne. La frise s'arrête, sans alerte (chantier 005).
          // La confirmation « Rangé dans… » ne dit plus vrai : rien n'est enregistré, l'écran redevient prêt à l'envoi.
          if (etape?.code === CODE_ANNULE) { setAnnule(true); setConfirmation(null); return }
          // Le code de l'échec se traduit ; le texte brut du serveur n'est montré que sans code.
          setError(etape?.code ? traduireEtape(lang, etape) : streamError)
        },
      })
    } catch (caught) {
      if (caught instanceof DoublonError) setDoublon({ jobId: caught.jobId, titre: caught.titre })
      else if (suivi) setErreurEnvoi(String(caught))
      else setError(String(caught))
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
        <div className="transcribe-form-column">
          <section className="studio-panel file-step" aria-labelledby="file-step-title">
            <div className="studio-panel-heading">
              <span className="step-number">01</span>
              <div><h2 id="file-step-title">{t('transcribe.file.title')}</h2><p>{t('transcribe.file.subtitle')}</p></div>
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
            <RangerDans projets={projects} destination={destinationProjectId}
                        surChoisir={onChooseDestination} desactive={envoi}
                        surCreer={onCreateDestination ?? (() => Promise.reject(new Error('no-create')))}
                        proposee={destinationSuggested} nomParDefaut={libelleProjet(config.default_project, t)} />
          </section>

          {/* La section reste affichée tant qu'un envoi accepté est confirmé, même fichier vidé : la confirmation
              et le bouton « Transcrire un autre enregistrement » y vivent. */}
          {(file || confirmation) && (
            <section className="studio-panel options-step" aria-labelledby="options-step-title">
              <div className="studio-panel-heading">
                <span className="step-number">02</span>
                <div><h2 id="options-step-title">{t('transcribe.options.title')}</h2><p>{t('transcribe.options.subtitle')}</p></div>
              </div>

              <div className="stt-engine-section">
                <label className="field-label"><strong>{t('transcribe.mode.label')}</strong></label>
                <div className="enhancement-grid engine-toggle-grid">
                  <button
                    type="button"
                    className={`option-tile ${mode === 'local' ? 'on' : ''}`}
                    disabled={envoi}
                    onClick={() => setMode('local')}
                  >
                    <span className="option-icon"><Icon name="cpu" /></span>
                    <span>
                      <strong>{t('transcribe.mode.local')}</strong>
                      <small>{t('transcribe.mode.localDetail', {
                        // Le modèle du mode LOCAL, pas celui du fournisseur
                        // courant : ce libellé décrit une option qu'on peut
                        // lire depuis le mode cloud.
                        llm: config.llm_models?.[config.default_llm_provider || 'mlx']
                          || config.llm_model || 'MLX',
                      })}</small>
                    </span>
                  </button>
                  <button
                    type="button"
                    className={`option-tile ${mode === 'cloud' ? 'on llm' : ''}`}
                    disabled={envoi || !cloudPret || ultra}
                    onClick={() => setMode('cloud')}
                  >
                    <span className="option-icon"><Icon name="sparkles" /></span>
                    <span>
                      <strong>{t('transcribe.mode.cloud')}</strong>
                      <small>
                        {ultra
                          ? t('transcribe.ultra.cloudOff')
                          : !cloudPret
                          ? t('transcribe.llm.missingMistral')
                          : (mode === 'cloud' && fournisseurCle !== 'mistral')
                            ? t('transcribe.cloud.detail', { stt: sttCourt, llm: llmNom })
                            : t('transcribe.mode.cloudDetail')}
                      </small>
                    </span>
                  </button>
                </div>
                {/* Sélecteur de fournisseur cloud : SECONDAIRE, visible seulement en
                    mode cloud quand PLUSIEURS fournisseurs cloud sont prêts. Piloté
                    par le registre (`readyCloud`), aucun « == mistral » en dur. */}
                {mode === 'local' && readyLocal.length > 1 && (
                  <div className="local-provider-select" style={{ marginTop: '8px' }}>
                    <div className="enhancement-grid" style={{ gridTemplateColumns: `repeat(${readyLocal.length}, 1fr)` }}>
                      {readyLocal.map((p) => (
                        <button key={p} type="button" className={`option-tile ${fournisseurCle === p ? 'on llm' : ''}`}
                          disabled={envoi} onClick={() => { setLlmProvider(p); setEngine('whisper') }}>
                          <span>
                            <strong>{p === 'compatible' ? t('app.provider.compatible') : (config.llm_models?.[p] || p)}</strong>
                            <small>{config.llm_models?.[p] || p}</small>
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {mode === 'cloud' && readyCloud.length > 1 && (
                  <div className="cloud-provider-select" style={{ marginTop: '8px' }}>
                    <label className="field-label"><small>{t('transcribe.cloud.provider')}</small></label>
                    <div
                      className="enhancement-grid"
                      style={{ gridTemplateColumns: `repeat(${readyCloud.length}, 1fr)` }}
                    >
                      {readyCloud.map((p) => (
                        <button
                          key={p}
                          type="button"
                          className={`option-tile ${fournisseurCle === p ? 'on llm' : ''}`}
                          disabled={envoi}
                          onClick={() => setCloudProvider(p)}
                        >
                          <span>
                            <strong>{nomCloud(p)}</strong>
                            <small>{prives.includes(p) ? `${t('app.mode.privateCloud')} · ` : ''}{config.llm_models?.[p] || p}</small>
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <label className="field">{t('spoken.label')}
                <select className="option-select" aria-label={t('spoken.label')} value={languageChoice}
                        onChange={(event) => {
                          const value = event.target.value
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
                         onChange={(event) => setLanguage(event.target.value.trim().toLowerCase())} />
                </label>
              )}

              {/* Sensibilité du document. Petit sélecteur, sous-titré pour DIRE que
                  c'est la nature du document, pas le fournisseur d'IA ; en
                  ultra-confidentiel, il garde tout en local. */}
              <label className="field">{t('sensitivity.label')}
                <select className="option-select" aria-label={t('sensitivity.label')}
                        value={sensitivity} disabled={envoi}
                        onChange={(event) => setSensitivity(event.target.value)}>
                  {sensitivityLevels.map((niveau) => (
                    <option key={niveau} value={niveau}>{t(sensitivityLabelKey(niveau))}</option>
                  ))}
                </select>
                <small className="field-note">{t(ultra ? cleSelonFaits('sensitivity.hint.ultra', config.faits === true)
                  : ultraCloudAutorise ? 'sensitivity.hint.ultraCloud' : 'sensitivity.hint')}</small>
              </label>

              <div className="enhancement-grid">
                <label className={`option-tile ${diarize && diarisationPossible ? 'on' : ''} ${diarisationPossible ? '' : 'disabled'}`}>
                  <input type="checkbox" checked={diarize && diarisationPossible}
                         disabled={envoi || !diarisationPossible}
                         onChange={(event) => setDiarize(event.target.checked)} />
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
                         onChange={(event) => setProfile(event.target.checked)} />
                  <span className="option-icon"><Icon name="sparkles" /></span>
                  <span><strong>{t('transcribe.profile.label')}</strong><small>{llmPret ? t('transcribe.profile.detail', { llm: llmNom }) : llmManquant}</small></span>
                </label>
                <label className={`option-tile ${reportOn ? 'on llm' : ''} ${llmPret && timestamps ? '' : 'disabled'}`}>
                  <input type="checkbox" checked={reportOn} disabled={envoi || !llmPret || !timestamps}
                         onChange={(event) => setReportOn(event.target.checked)} />
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
              {error && <div className="error job-error" role="alert"><Icon name="error" /><span><strong>{t('transcribe.aborted')}</strong>{error}</span></div>}
              {erreurEnvoi && <div className="error job-error" role="alert"><Icon name="error" /><span><strong>{t('transcribe.aborted')}</strong>{erreurEnvoi}</span></div>}
            </section>
          )}

          {/* Le compagnon s'explique ici, sans être piloté depuis la page. */}
          <section className="studio-panel companion-note" aria-labelledby="companion-title">
            <h2 id="companion-title">{t('transcribe.companion.title')}</h2>
            <p>{t('transcribe.companion.body')}</p>
            <p>{t('transcribe.companion.headset')}</p>
            <p className="muted small">{t('transcribe.companion.install', { readme: 'compagnon/README.md' })}</p>
          </section>
        </div>

        <aside className="studio-panel process-panel" aria-labelledby="process-title">
          <div className="studio-panel-heading">
            <span className="step-number">03</span>
            <div><h2 id="process-title">{t('transcribe.process.title')}</h2><p>{annule ? t('traitements.cancelled.message')
              : suiviEnAttente ? t('traitements.waiting.hint')
                : started ? rendreLigne(t, lang, ligne) || t('transcribe.process.init') : t('transcribe.process.idle')}</p></div>
          </div>

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
      </div>
    </section>
  )
}
