import { getLlmProvider } from './lib/llmProvider'
import type { EtapeJob } from './lib/i18n'
// Client API typé vers le backend FastAPI (proxifié sur /api par Vite).

/** Portée transversale : tous les projets du compte. */
export const PORTEE_TOUS = 'all'

/** Portée de lecture : ce qui est partagé avec le compte (« Partagés avec moi »). */
export const PORTEE_PARTAGES = 'shared'

/**
 * Portée d'OUVERTURE d'un document, d'après ce que le document déclare.
 *
 * Elle vient du document — `SearchResult.project_id`, `RagSource.project_id`,
 * `LibraryItem.project_id` — et JAMAIS du projet courant. La Recherche et
 * l'Assistant savent élargir leur portée à `all` par un bouton dont l'état est
 * LOCAL au composant : ni l'URL ni le parent ne le connaissent. Rouvrir un
 * résultat avec la portée du projet courant le rendrait donc introuvable.
 *
 * Un `project_id` nul — document non migré, ou hors projet — n'a aucune portée
 * étroite qui le contienne : `all` est la seule qui le trouve à coup sûr.
 */
export const porteeDocument = (projectId: string | null | undefined): string =>
  projectId || PORTEE_TOUS

/** Ajoute la portée en query string ; une portée vide laisse le backend décider. */
const withViewScope = (path: string, viewScope = '') =>
  path + (viewScope ? `?view_scope=${encodeURIComponent(viewScope)}` : '')

export interface AppConfig {
  engines: string[]
  /** Option des faits de la réunion (`MEMOGLYPH_FAITS=1`) ; absente ou fausse : onglet masqué. */
  faits?: boolean
  default_engine: string
  llm_providers?: string[]
  local_providers?: string[]
  /** Serveurs hébergés par l'utilisateur hors de la boucle locale : « cloud privé ». */
  private_cloud_providers?: string[]
  default_llm_provider?: string
  default_project: string
  default_project_id: string
  whisper_model: string
  llm_model: string
  mistral_chat_model?: string
  mistral_stt_model?: string
  max_upload_mb: number
  supported_extensions: string[]
  diarization_available: boolean
  llm_available: boolean
  /**
   * `{fournisseur → prêt}` et `{fournisseur → modèle servi}`, construits depuis
   * le REGISTRE du backend. Optionnels parce qu'un backend antérieur ne les
   * envoie pas ; le code retombe alors sur les deux champs hérités ci-dessous.
   *
   * Ils remplacent un couple de booléens qui ne connaissait que deux moteurs :
   * MLX pouvait être servi par le backend et rester invisible ici.
   */
  llm_ready?: Record<string, boolean>
  llm_models?: Record<string, string>
  /**
   * Vocabulaire FERMÉ de sensibilité du document, du plus sensible au plus
   * ouvert. Piloté par le backend (`core/sensitivity`) : l'UI ne code aucune
   * valeur en dur. MÉTADONNÉE seulement — aucun effet sur le fournisseur ni
   * l'exécution aujourd'hui. Optionnels pour un backend antérieur.
   */
  sensitivity_levels?: string[]
  default_sensitivity?: string
}

/** Niveaux de sensibilité (identifiants stables ; libellés via i18n). */
export type Sensitivity = 'ultra_confidential' | 'confidential' | 'standard'

export interface LibraryItem {
  name: string
  /** Identifiant STABLE, lu du front-matter. Survit au renommage, contrairement à `name`. */
  id: string | null
  meeting_id: string | null
  project_id: string | null
  title: string
  date: string
  duration: string | null
  language: string | null
  engine: string | null
  source: string | null
  project: string
  diarization: boolean
  profiled: boolean
  n_speakers: number | null
  /** « report » pour un compte-rendu, null pour un transcript. */
  type: string | null
  /** Nom du .md parent — renseigné uniquement sur un compte-rendu. */
  source_transcript: string | null
  /** Identité stable du transcript parent ; autoritaire sur son nom. */
  source_document_id: string | null
  /** Nombre de réserves de qualité (dérivé à l'indexation, jamais écrit dans le .md). */
  warnings: number
  size: number
  modified: number
}

export interface QualityReservation {
  document_id: string | null
  name: string
  count: number
}

export interface Meeting {
  id: string
  title: string
  project_id: string | null
  project: string
  source: string | null
  date: string
  generated_at: string | null
  modified: number
  duration: string | null
  language: string | null
  diarization: boolean
  profiled: boolean
  n_speakers: number | null
  transcripts: LibraryItem[]
  reports: LibraryItem[]
  preferred_transcript: LibraryItem | null
  quality_reservations: QualityReservation[]
}

// ---- Projets (un projet = un dossier de output/) ----
export interface Project {
  id: string | null
  project: string
  name: string
  kind: 'inbox' | 'regular'
  /** Compte propriétaire ; `null` = projet d'avant les comptes, vu des seuls administrateurs. */
  owner?: string | null
  count: number
}

export const getProjects = () => fetch('/api/projects').then(j<Project[]>)

export const createProject = (name: string) =>
  fetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  }).then(j<Project>)

// `viewScope` est OBLIGATOIRE sur tout ce qui vise UN document précis. Un
// paramètre optionnel est un paramètre qu'on oubliera — et l'oubli ne se voit
// pas : il retombe sur la boîte de réception et rend un 404 muet sur un
// document parfaitement présent, ailleurs.
export const moveTranscript = (
  name: string, destinationProjectId: string, viewScope: string,
) =>
  fetch(withViewScope(`/api/transcript/${encodeURIComponent(name)}/project`, viewScope), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ destination_project_id: destinationProjectId }),
  }).then(j<{ name: string; project: string; project_id: string }>)

export const moveMeeting = (
  meetingId: string, destinationProjectId: string, viewScope: string,
) =>
  fetch(withViewScope(`/api/meetings/${encodeURIComponent(meetingId)}/project`, viewScope), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ destination_project_id: destinationProjectId }),
  }).then(j<Meeting>)

/** Change la sensibilité de la réunion de ce document, pour toutes ses versions ; rend le niveau appliqué. */
export const updateSensitivity = (
  name: string, sensitivity: string, viewScope: string,
) =>
  fetch(withViewScope(`/api/transcript/${encodeURIComponent(name)}/sensitivity`, viewScope), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sensitivity }),
  }).then(j<{ name: string; sensitivity: string }>)

export const archiveMeeting = (meetingId: string, viewScope: string) =>
  fetch(withViewScope(`/api/meetings/${encodeURIComponent(meetingId)}`, viewScope),
        { method: 'DELETE' })
    .then(j<{ archived_meeting: string; documents: string[] }>)

export type LabelSource = 'auto' | 'manual' | null

export interface VoiceAssignment {
  voice_id: number
  label: string | null
  label_source: LabelSource
  display: string
}

export interface TranscriptDetail {
  name: string
  meta: Record<string, unknown>
  body: string
  content: string
  voices?: Record<string, VoiceAssignment>
  /** Sensibilité EFFECTIVE, celle de la réunion, calculée par le serveur ; `meta.sensitivity` est le niveau noté à la création du document. */
  sensitivity_effective?: string
  /** Un audio a-t-il été conservé ? (les transcripts antérieurs n'en ont pas) */
  has_audio?: boolean
  /**
   * Part de segments dont la langue s'écarte de celle déclarée.
   * `null` = NON VÉRIFIÉ (paire de langues non couverte) — à ne pas afficher
   * comme « propre », ce serait une garantie qu'on n'a pas.
   */
  language_mix?: number | null
  /** Le mélange a été mesuré ET dépasse le seuil d'alerte. */
  language_unstable?: boolean
}

export interface TranscribeOptions {
  engine: string
  language: string
  diarize: boolean
  profile: boolean
  report_on?: boolean
  timestamps: boolean
  destinationProjectId: string
  mistralApiKey?: string
  llm_provider?: string
  /** Sensibilité choisie à l'enregistrement (métadonnée, sans effet exécution). */
  sensitivity?: string
}

// ---------- Traitements (chantier 003, tranche B) ----------

export type StatutTraitement = 'pending' | 'running' | 'done' | 'error'

/** Un traitement du compte, tel que `GET /api/jobs` le décrit : léger, sans texte de transcript. */
export interface Traitement {
  id: string
  nature: string
  statut: StatutTraitement
  /** Vrai tant que le créneau de calcul lourd n'est pas obtenu : le traitement n'a pas commencé. */
  en_attente: boolean
  progression: number
  titre: string
  projet_id: string | null
  projet_nom: string
  etape: number | null
  etapes: number | null
  message_code: string | null
  message_params: Record<string, string | number>
  /** Secondes depuis l'époque. */
  debut: number
  meeting: { name: string; meeting_id?: string; project_id?: string } | null
  erreur: string | null
  erreur_code: string | null
  erreur_params: Record<string, string | number>
}

export interface ListeTraitements {
  traitements: Traitement[]
  actifs: number
  en_attente: number
}

export const getTraitements = () => fetch('/api/jobs').then(j<ListeTraitements>)

export type JobEvent =
  | {
      type: 'progress'
      progress: number
      message: string
      code?: string | null
      params?: Record<string, string | number>
    }
  | { type: 'partial'; text: string }
  | { type: 'warning'; text: string }
  | { type: 'done'; result: { name: string; content: string; warnings?: string[] } }
  | { type: 'error'; error: string; code?: string | null; params?: Record<string, string | number> }

export interface JobStatus {
  id: string
  status: 'pending' | 'running' | 'done' | 'error'
  progress: number
  message: string
  message_code?: string | null
  message_params?: Record<string, string | number>
  result: { name: string; content: string } | null
  error: string | null
  error_code?: string | null
  error_params?: Record<string, string | number>
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<T>
}

export interface LibraryQuery {
  q?: string
  sort?: 'modified' | 'title' | 'duration' | 'date' | 'language'
  order?: 'asc' | 'desc'
  language?: string
  diarized?: boolean
  profiled?: boolean
  viewScope?: string
}

export const getConfig = () => fetch('/api/config').then(j<AppConfig>)

// ---------- Authentification ----------

export interface Utilisateur { identifiant: string; admin: boolean }

export interface EtatAuth {
  installation_requise: boolean
  connecte: boolean
  utilisateur: Utilisateur | null
}

const postJson = (url: string, corps: unknown) => fetch(url, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(corps),
})

/** Message lisible d'un refus du backend (`detail` de FastAPI). */
async function refus(r: Response): Promise<never> {
  let detail = `HTTP ${r.status}`
  try {
    const corps = await r.json()
    if (typeof corps?.detail === 'string') detail = corps.detail
  } catch { /* corps non JSON : le statut suffit */ }
  throw new Error(detail)
}

export const getEtatAuth = () => fetch('/api/auth/etat').then(j<EtatAuth>)

export async function installer(code: string, identifiant: string, motDePasse: string) {
  const r = await postJson('/api/auth/installation', { code, identifiant, mot_de_passe: motDePasse })
  return r.ok ? (r.json() as Promise<{ utilisateur: Utilisateur }>) : refus(r)
}

export async function connecter(identifiant: string, motDePasse: string) {
  const r = await postJson('/api/auth/connexion', { identifiant, mot_de_passe: motDePasse })
  return r.ok ? (r.json() as Promise<{ utilisateur: Utilisateur }>) : refus(r)
}

export const deconnecter = () => postJson('/api/auth/deconnexion', {}).then(() => undefined)

export interface Compte { identifiant: string; admin: boolean; actif: boolean; cree_le: string }

export async function listerComptes(): Promise<Compte[]> {
  const r = await fetch('/api/admin/utilisateurs')
  return r.ok ? (r.json() as Promise<Compte[]>) : refus(r)
}

export async function creerCompte(identifiant: string, motDePasse: string, admin: boolean) {
  const r = await postJson('/api/admin/utilisateurs', { identifiant, mot_de_passe: motDePasse, admin })
  return r.ok ? (r.json() as Promise<{ utilisateur: Utilisateur }>) : refus(r)
}

/** Jeton d'appareil du compagnon de capture : le secret n'est rendu qu'à la création. */
export interface JetonAppareil { id: string; nom: string; cree_le: string; dernier_usage: string | null }

export async function listerJetons(): Promise<JetonAppareil[]> {
  const r = await fetch('/api/jetons')
  return r.ok ? (r.json() as Promise<JetonAppareil[]>) : refus(r)
}

export async function creerJeton(nom: string) {
  const r = await postJson('/api/jetons', { nom })
  return r.ok ? (r.json() as Promise<{ jeton: JetonAppareil; secret: string }>) : refus(r)
}

export async function retirerJeton(jetonId: string): Promise<void> {
  const r = await fetch(`/api/jetons/${encodeURIComponent(jetonId)}`, { method: 'DELETE' })
  if (!r.ok) await refus(r)
}

/** Réglage du compte : le cloud est-il autorisé pour SES documents ultra-confidentiels ? */
export interface ReglageCloudUltra { autorise: boolean; modifie_le: string | null }

export async function getCloudUltra(): Promise<ReglageCloudUltra> {
  const r = await fetch('/api/compte/cloud-ultra')
  return r.ok ? (r.json() as Promise<ReglageCloudUltra>) : refus(r)
}

export async function setCloudUltra(autorise: boolean): Promise<ReglageCloudUltra> {
  const r = await fetch('/api/compte/cloud-ultra', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ autorise }),
  })
  return r.ok ? (r.json() as Promise<ReglageCloudUltra>) : refus(r)
}

export async function changerActivation(identifiant: string, actif: boolean): Promise<void> {
  const action = actif ? 'reactivation' : 'desactivation'
  const r = await postJson(`/api/admin/utilisateurs/${encodeURIComponent(identifiant)}/${action}`, {})
  if (!r.ok) await refus(r)
}

export function getLibrary(query: LibraryQuery = {}): Promise<LibraryItem[]> {
  const p = new URLSearchParams()
  if (query.q) p.set('q', query.q)
  if (query.sort) p.set('sort', query.sort)
  if (query.order) p.set('order', query.order)
  if (query.language) p.set('language', query.language)
  if (query.diarized) p.set('diarized', 'true')
  if (query.profiled) p.set('profiled', 'true')
  if (query.viewScope) p.set('view_scope', query.viewScope)
  const qs = p.toString()
  return fetch(`/api/library${qs ? '?' + qs : ''}`).then(j<LibraryItem[]>)
}

export function getMeetings(query: LibraryQuery = {}): Promise<Meeting[]> {
  const p = new URLSearchParams()
  if (query.q) p.set('q', query.q)
  if (query.sort) p.set('sort', query.sort)
  if (query.order) p.set('order', query.order)
  if (query.language) p.set('language', query.language)
  if (query.diarized) p.set('diarized', 'true')
  if (query.profiled) p.set('profiled', 'true')
  if (query.viewScope) p.set('view_scope', query.viewScope)
  const qs = p.toString()
  return fetch(`/api/meetings${qs ? '?' + qs : ''}`).then(j<Meeting[]>)
}

export const getLanguages = () => fetch('/api/library/languages').then(j<string[]>)

/** Un fait de l'ECL tel que l'écran l'affiche : la citation d'abord, l'énoncé en second. */
export interface FaitEcl {
  id: string
  document_id: string
  document: string
  type: 'decision' | 'action' | 'probleme' | 'chiffre' | string
  horodatage: string
  secondes: number
  citation: string
  enonce: string
  enonce_statut: string
  porteur: string | null
  locuteur: string | null
  modele: string | null
  /** « local » ou « cloud » ; null si aucun fournisseur connu ne sert ce modèle. */
  mode: 'local' | 'cloud' | null
  a_verifier: boolean
  /** Extraction partielle : tranches en échec sur tranches ; absente ou null si complète. */
  partielle?: { tranches_en_echec: number, tranches: number } | null
  /** Fournisseur qui a extrait le fait (mlx, openai…), quand il est noté. */
  fournisseur?: string | null
  /** Affichage C (local) : la citation, étendue à la réplique suivante quand elle est courte. */
  affiche?: string | null
  /** « perime » : la source a changé depuis l'extraction et le fait n'y passe plus le contrôle. */
  statut?: 'actuel' | 'perime'
  /** Motif du contrôle échoué d'un fait périmé (`citation_absente`, `contenance`…). */
  motif_peremption?: string | null
}

/** Relance l'extraction des faits d'une réunion avec ce fournisseur ; rend la tâche de fond. */
export const relancerFaitsReunion = (meetingId: string, viewScope: string, provider: string) =>
  fetch(withViewScope(`/api/meetings/${encodeURIComponent(meetingId)}/ecl`, viewScope), {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider }),
  }).then(j<{ job_id: string }>)

export const getFaitsReunion = (meetingId: string, viewScope: string) =>
  fetch(withViewScope(`/api/meetings/${encodeURIComponent(meetingId)}/ecl`, viewScope))
    .then(j<{ meeting_id: string; faits: FaitEcl[]; perimes?: FaitEcl[] }>)

export type SearchMode = 'fts' | 'semantic'

export interface SearchResult {
  name: string
  document_id: string | null
  meeting_id: string | null
  project_id: string | null
  project: string | null
  title: string
  source: string | null
  language: string | null
  duration: string | null
  diarization: boolean
  profiled: boolean
  n_speakers: number | null
  date?: string
  ts?: string | null
  snippet: string
  // Rang de fusion en mode sémantique, pas une similarité : la recherche
  // combine un classement lexical et un classement dense, et aucune
  // distance ne décide plus seule de l'ordre. Absent en mode lexical pur.
  rang?: number
  // D'où vient le passage : la parole transcrite, ou un compte-rendu (généré).
  origine?: 'transcript' | 'compte-rendu'
}

export const search = (q: string, mode: SearchMode = 'fts', viewScope = '') =>
  fetch(
    `/api/search?q=${encodeURIComponent(q)}&mode=${mode}` +
      (viewScope ? `&view_scope=${encodeURIComponent(viewScope)}` : ''),
  ).then(j<SearchResult[]>)

export interface RagSource {
  n: number
  name: string
  title: string
  ts: string | null
  // Rang de fusion, pas une similarité : le RAG mêle un classement lexical et
  // un classement dense, et un score RRF ne se compare pas d'une requête à
  // l'autre. `SearchResult.score` reste une similarité,
  // lui — la recherche sémantique n'a pas changé.
  rang: number | null
  origine?: 'transcript' | 'compte-rendu'
  excerpt: string
  document_id: string | null
  meeting_id: string | null
  project_id: string | null
  project: string | null
}
export interface RagAnswer {
  answer: string
  sources: RagSource[]
  /** Passages ultra-confidentiels gardés hors de l'envoi au fournisseur cloud. */
  passages_ecartes?: number
}

export type RagScope =
  | { mode: 'project'; project_id: string }
  | { mode: 'all' }
  | { mode: 'shared'; project_id?: string }

export const ask = (question: string, scope: RagScope) =>
  fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, scope, llm_provider: getLlmProvider() }),
  }).then(j<RagAnswer>)

export interface DupMember {
  name: string
  title: string
  source: string | null
  language: string | null
  duration: string | null
  diarization: boolean
  profiled: boolean
  n_speakers: number | null
  generated_at: string | null
  keep: boolean
}
export interface DupGroup {
  keep: string
  reason: string
  count: number
  source: string | null
  members: DupMember[]
}

export const getDuplicates = (viewScope = '') =>
  fetch('/api/duplicates' + (viewScope ? `?view_scope=${encodeURIComponent(viewScope)}` : ''))
    .then(j<DupGroup[]>)

// ---- Corbeille / suppression définitive ----
export interface TrashItem {
  name: string
  origin: string
  project: string | null
  document_id: string | null
  project_id: string | null
  meeting_id: string | null
  title: string
  /** Média d'origine (celui du transcript, pour un compte-rendu). */
  source?: string | null
  type: string | null
  source_transcript: string | null
  source_document_id: string | null
  archived_at: string | null
  size: number
  modified: number
}

export const getTrash = (viewScope = '') =>
  fetch('/api/trash' + (viewScope ? `?view_scope=${encodeURIComponent(viewScope)}` : ''))
    .then(j<TrashItem[]>)

export const restoreTrashItem = (archivedName: string, viewScope = '') =>
  fetch(
    `/api/trash/${encodeURIComponent(archivedName)}/restore`
      + (viewScope ? `?view_scope=${encodeURIComponent(viewScope)}` : ''),
    { method: 'POST' },
  ).then(j<{ restored: string; project: string; warnings: string[] }>)

export const deleteTrashItem = (archivedName: string, viewScope = '') =>
  fetch(
    `/api/trash/${encodeURIComponent(archivedName)}`
      + (viewScope ? `?view_scope=${encodeURIComponent(viewScope)}` : ''),
    { method: 'DELETE' },
  ).then(j<{
    deleted: string
    origin: string
    clean: boolean
    shared_artifacts_preserved: boolean
    freed_bytes: number
    errors: string[]
  }>)

export const emptyTrash = (viewScope = '') =>
  fetch(
    '/api/trash' + (viewScope ? `?view_scope=${encodeURIComponent(viewScope)}` : ''),
    { method: 'DELETE' },
  ).then(j<{ removed: number; freed_bytes: number; kept: string[]; errors: string[] }>)

/** Suppression DÉFINITIVE : fichier, index, vecteurs, voix, empreintes, audio. */
export const deleteTranscript = (name: string, viewScope = '') =>
  fetch(
    `/api/transcript/${encodeURIComponent(name)}?hard=true`
      + (viewScope ? `&view_scope=${encodeURIComponent(viewScope)}` : ''),
    { method: 'DELETE' },
  ).then(j<{ deleted: string; documents: string[]; clean: boolean }>)
export interface ArchivePlanDocument {
  name: string
  title: string
  type: string | null
  document_id: string | null
}
// Ces deux routes sont bornées côté backend DEPUIS leur écriture : la portée
// n'y est pas une préparation, c'est ce qui les fait répondre.
export const getArchivePlan = (name: string, viewScope: string) =>
  fetch(withViewScope(`/api/transcript/${encodeURIComponent(name)}/archive-plan`, viewScope))
    .then(j<{ requested: string; documents: ArchivePlanDocument[] }>)
export const archiveTranscript = (name: string, viewScope: string) =>
  fetch(withViewScope(`/api/transcript/${encodeURIComponent(name)}`, viewScope),
        { method: 'DELETE' })
    .then(j<{ archived: string; documents: string[] }>)

export const getTranscript = (name: string, viewScope: string) =>
  fetch(withViewScope(`/api/transcript/${encodeURIComponent(name)}`, viewScope))
    .then(j<TranscriptDetail>)

/** FinOps (lecture seule) — usage & coût d'UN document. `available:false` si pas de télémétrie. */
export interface FinopsSummary {
  available: boolean
  execution_id?: string
  kind?: string
  status?: string | null
  provider?: string | null
  model?: string | null
  cost?: number | null
  currency?: string | null
  cost_basis?: string | null            // cloud_derived | local_imputed | mixed | null
  pricing_provenance?: string | null
  calls?: number | null
  in_tok?: number | null
  out_tok?: number | null
  tokens?: number | null
  audio_seconds?: number | null
  wall_clock_s?: number | null
  started_at?: string | null
  ended_at?: string | null
  /** Transcript seulement : son exécution de profilage, au même contrat, quand elle existe. */
  profile?: FinopsSummary
}
export interface FinopsCall {
  seq: number
  stage: string | null
  provider: string | null
  model: string | null
  latency_s: number | null
  status: string | null
  usage_source: string | null
  in_tok: number | null
  out_tok: number | null
  cached_tok: number | null
  reasoning_tok: number | null
  cost: number | null
  cost_currency: string | null
  cost_basis: string | null
}
export interface FinopsExecutionDetail {
  available: boolean
  execution?: FinopsSummary
  calls?: FinopsCall[]
}
export const getFinopsSummary = (name: string, viewScope: string) =>
  fetch(withViewScope(`/api/transcript/${encodeURIComponent(name)}/finops`, viewScope))
    .then(j<FinopsSummary>)
export const getFinopsExecution = (name: string, executionId: string, viewScope: string) =>
  fetch(withViewScope(
    `/api/transcript/${encodeURIComponent(name)}/finops/execution/${encodeURIComponent(executionId)}`,
    viewScope))
    .then(j<FinopsExecutionDetail>)
// ---------- Usage (tableau de bord FinOps, phase 3B — lecture seule, borné projet) ----------

export interface UsageRollupRow {
  group_by: string
  bucket: string | null
  currency: string | null
  cost_basis: string | null           // cloud_derived | local_imputed | mixed | null (non tarifé)
  n_executions: number
  n_model_calls: number
  in_tok: number
  out_tok: number
  cached_tok: number
  reasoning_tok: number
  audio_seconds: number
  wall_clock_s: number
  cost: number | null                 // null = non tarifé (jamais un faux zéro)
}
export interface UsageRollup {
  group_by: string
  scope: string
  rows: UsageRollupRow[]
}
export type UsageGroupBy =
  | 'day' | 'project_id' | 'provider' | 'model' | 'stage' | 'exec_kind' | 'cost_basis'

export interface UsageFilters {
  start?: string
  end?: string
  provider?: string
  model?: string
  stage?: string
  kind?: string
}

const usageQuery = (viewScope: string, params: Record<string, string | number | undefined>) => {
  const q = new URLSearchParams()
  if (viewScope) q.set('view_scope', viewScope)
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') q.set(k, String(v))
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}

export const getUsageRollup = (
  viewScope: string, groupBy: UsageGroupBy, filters: UsageFilters = {},
) =>
  fetch(`/api/usage/rollup${usageQuery(viewScope, { group_by: groupBy, ...filters })}`)
    .then(j<UsageRollup>)

export interface UsageExecution {
  execution_id: string
  kind: string | null
  status: string | null
  project_id: string | null
  provider_primary: string | null
  model_primary: string | null
  started_at: string | null
  ended_at: string | null
  wall_clock_s: number | null
  n_calls: number | null
  in_tok: number | null
  out_tok: number | null
  audio_seconds: number | null
  cost: number | null
  cost_currency: string | null
  cost_basis: string | null
  pricing_provenance?: string | null
}
export interface UsageExecutionsPage {
  executions: UsageExecution[]
  limit: number
  offset: number
  has_more: boolean
  scope: string
}
export const getUsageExecutions = (
  viewScope: string, filters: UsageFilters = {}, limit = 25, offset = 0,
) =>
  fetch(`/api/usage/executions${usageQuery(viewScope, { ...filters, limit, offset })}`)
    .then(j<UsageExecutionsPage>)

export const getUsageExecutionDetail = (viewScope: string, executionId: string) =>
  fetch(withViewScope(`/api/usage/executions/${encodeURIComponent(executionId)}`, viewScope))
    .then(j<FinopsExecutionDetail>)

export const downloadUrl = (name: string, viewScope: string) =>
  withViewScope(`/api/transcript/${encodeURIComponent(name)}/download`, viewScope)

/** Audio conservé du transcript (archive compressée) — pour le lecteur synchronisé. */
export const audioUrl = (name: string, viewScope: string) =>
  withViewScope(`/api/transcript/${encodeURIComponent(name)}/audio`, viewScope)

export interface ReportResult {
  name: string
  content: string
  genre: string
}

/** `provider` : imposé par l'appelant (un document ultra-confidentiel reste local),
 * sinon le fournisseur choisi dans l'interface. */
export const makeReport = (name: string, viewScope: string, provider?: string) =>
  fetch(withViewScope(`/api/transcript/${encodeURIComponent(name)}/report`, viewScope), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ llm_provider: provider || getLlmProvider() }),
  })
    .then(j<ReportResult>)

/** Régénère un compte rendu comme un TRAITEMENT suivi (vue des traitements, indicateur) ; rend son identifiant.
 * La nouvelle version s'ajoute aux existantes. Les refus (403 règle d'envoi, 429 trop de travail) sont des
 * erreurs `HTTP <statut>: …`, lues par l'appelant. */
export const lancerCompteRendu = (name: string, viewScope: string, provider: string) =>
  fetch(withViewScope(`/api/transcript/${encodeURIComponent(name)}/report/jobs`, viewScope), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ llm_provider: provider }),
  }).then(j<{ job_id: string }>)

// ---- Voix (signature vocale) ----
export interface VoiceOccurrence {
  name: string
  document_id: string | null
  title: string
  /** Média d'origine : le titre lisible en vient quand le titre est resté un slug. */
  source?: string | null
  speaker_local: string
  project_id: string | null
  project: string | null
}
export interface Voice {
  id: number
  label: string | null
  label_source: LabelSource
  /** Nom proposé d'après le profil LLM — une hypothèse à valider, jamais appliquée seule. */
  suggestion: string | null
  display: string
  n_members: number
  occurrences: VoiceOccurrence[]
}

export const getVoices = (viewScope = '') =>
  fetch(withViewScope('/api/voices', viewScope)).then(j<Voice[]>)

export const renameVoice = (id: number, label: string | null, viewScope = '') =>
  fetch(withViewScope(`/api/voices/${id}`, viewScope), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label }),
  }).then(j<{ id: number; label: string | null }>)

export const mergeVoices = (id: number, targetId: number, viewScope = '') =>
  fetch(withViewScope(`/api/voices/${id}/merge`, viewScope), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_id: targetId }),
  }).then(j<{ merged: number; into: number }>)

export const previewBackfill = (viewScope = '') =>
  fetch(withViewScope('/api/voices/backfill/preview', viewScope)).then(j<{ eligible: string[] }>)

export const startBackfill = (viewScope = '') =>
  fetch(withViewScope('/api/voices/backfill', viewScope), { method: 'POST' }).then(j<{ job_id: string }>)

export interface MaintenanceStatus {
  documents: number
  diarized_documents: number
  semantic_documents: number
  semantic_chunks: number
  voice_documents: number
  voice_occurrences: number
  backfill_eligible: string[]
  faithful_backfill_eligible: string[]
  faithful_backfill_missing_originals: string[]
  duplicate_groups: number
  /**
   * État du CORPUS autoritaire, pas des index dérivés. Un corpus non conforme
   * bloque TOUTE réindexation de bibliothèque : suppressions, renommages et
   * déplacements cessent d'être pris en compte. L'échec ne partait qu'en
   * `print` au démarrage — invisible depuis l'interface.
   */
  corpus?: {
    ok: boolean
    issues: Record<string, string[]>
    erreur?: string
    reindexation_demarrage_ok?: boolean
    reindexation_demarrage_detail?: string
  }
  /**
   * Analyses qui n'ont pas pu aboutir (clé = analyse, valeur = cause). Une liste
   * vide parce qu'une analyse a ÉCHOUÉ n'est pas une liste vide parce qu'il n'y
   * a rien à signaler : sans ce champ, l'écran dirait « aucun écart » à tort.
   */
  analyses_indisponibles?: Record<string, string>
}

export const getMaintenanceStatus = (viewScope = '') =>
  fetch(withViewScope('/api/maintenance', viewScope)).then(j<MaintenanceStatus>)

export const reindexMaintenance = (kind: 'semantic' | 'voices', viewScope = '') =>
  fetch(withViewScope(`/api/maintenance/reindex/${kind}`, viewScope), { method: 'POST' })
    .then(j<Record<string, number>>)

export const startFaithfulBackfill = (viewScope = '') =>
  fetch(withViewScope('/api/maintenance/backfill/faithful', viewScope), { method: 'POST' })
    .then(j<{ job_id: string }>)

export async function submitTranscribe(
  file: File,
  opts: TranscribeOptions,
): Promise<string> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('engine', opts.engine)
  fd.append('language', opts.language)
  if (opts.mistralApiKey) {
    fd.append('mistral_api_key', opts.mistralApiKey)
  }
  if (opts.llm_provider) {
    fd.append('llm_provider', opts.llm_provider)
  }
  fd.append('diarize', String(opts.diarize))
  fd.append('profile', String(opts.profile))
  fd.append('report_on', String(opts.report_on ?? true))
  fd.append('timestamps', String(opts.timestamps))
  if (opts.sensitivity) {
    fd.append('sensitivity_level', opts.sensitivity)
  }
  fd.append('destination_project_id', opts.destinationProjectId)
  const { job_id } = await fetch('/api/transcribe', { method: 'POST', body: fd }).then(
    j<{ job_id: string }>,
  )
  return job_id
}

export const getJobStatus = (jobId: string) =>
  fetch(`/api/jobs/${encodeURIComponent(jobId)}`).then(j<JobStatus>)

// Ouvre un flux SSE de progression ; retourne une fonction d'arrêt.
export function streamJob(
  jobId: string,
  handlers: {
    // `etape` porte le code et les paramètres ; `msg` reste le repli anglais
    // du backend, pour un appelant qui ne veut pas traduire.
    onProgress?: (p: number, msg: string, etape: EtapeJob) => void
    onPartial?: (text: string) => void
    onWarning?: (text: string) => void
    onDone?: (result: { name: string; content: string; warnings?: string[] }) => void
    /** `etape` : le code de l'échec, que l'interface traduit ; `err` reste le texte brut du serveur. */
    onError?: (err: string, etape?: EtapeJob) => void
  },
): () => void {
  const es = new EventSource(`/api/jobs/${jobId}/events`)
  let stopped = false
  let settled = false
  let connected = false
  let recovering = false
  let recoveryTimer: ReturnType<typeof setTimeout> | null = null

  const clearRecoveryTimer = () => {
    if (recoveryTimer !== null) clearTimeout(recoveryTimer)
    recoveryTimer = null
  }
  const close = () => {
    clearRecoveryTimer()
    es.close()
  }
  const done = (result: { name: string; content: string }) => {
    if (settled || stopped) return
    settled = true
    handlers.onDone?.(result)
    close()
  }
  const fail = (error: string, etape?: EtapeJob) => {
    if (settled || stopped) return
    settled = true
    handlers.onError?.(error, etape)
    close()
  }
  const recoverFromStatus = async () => {
    if (recovering || settled || stopped || connected) return
    recovering = true
    try {
      const status = await getJobStatus(jobId)
      if (status.status === 'done') {
        if (status.result) done(status.result)
        else fail('Le job est terminé sans résultat récupérable.')
      } else if (status.status === 'error') {
        fail(status.error || 'Le traitement a échoué.', status.error_code
          ? { code: status.error_code, params: status.error_params ?? {}, message: status.error ?? '' } : undefined)
      } else {
        handlers.onProgress?.(status.progress, status.message, {
          code: status.message_code ?? null,
          params: status.message_params ?? {},
          message: status.message,
        })
      }
    } catch {
      // Le réseau et le flux peuvent tomber ensemble. EventSource retente de
      // lui-même ; le statut sera relu tant que la connexion ne revient pas.
    } finally {
      recovering = false
      if (!settled && !stopped && !connected) {
        recoveryTimer = setTimeout(recoverFromStatus, 1000)
      }
    }
  }

  es.onopen = () => {
    connected = true
    clearRecoveryTimer()
  }
  es.onmessage = (e) => {
    connected = true
    const ev = JSON.parse(e.data) as JobEvent
    if (ev.type === 'progress')
      handlers.onProgress?.(ev.progress, ev.message, {
        code: ev.code ?? null,
        params: ev.params ?? {},
        message: ev.message,
      })
    else if (ev.type === 'partial') handlers.onPartial?.(ev.text)
    // Une étape dérivée a échoué sans perdre le Markdown : à signaler, pas à taire.
    else if (ev.type === 'warning') handlers.onWarning?.(ev.text)
    else if (ev.type === 'done') done(ev.result)
    else if (ev.type === 'error') {
      fail(ev.error, ev.code ? { code: ev.code, params: ev.params ?? {}, message: ev.error } : undefined)
    }
  }
  es.onerror = () => {
    connected = false
    void recoverFromStatus()
  }
  return () => {
    if (stopped) return
    stopped = true
    close()
  }
}

// ---------- Partages v1 ----------

export interface Partage {
  id: string
  proprietaire: string
  invite: string
  type: 'projet' | 'reunion'
  cible: string
  cree_le: string
  /** Nom du projet, ou titre de la réunion ; nul si la cible a disparu. */
  nom: string | null
}

export interface Partages {
  donnes: Partage[]
  recus: Partage[]
}

export const getPartages = () => fetch('/api/partages').then(j<Partages>)

export const creerPartage = (type: Partage['type'], cible: string, invite: string) =>
  fetch('/api/partages', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type, cible, invite }),
  }).then(async (r) => (r.ok ? ((await r.json()) as { partage: Partage }) : refus(r)))

export const retirerPartage = (partageId: string) =>
  fetch(`/api/partages/${encodeURIComponent(partageId)}`, { method: 'DELETE' })
    .then(async (r) => (r.ok ? ((await r.json()) as { retire: string }) : refus(r)))
