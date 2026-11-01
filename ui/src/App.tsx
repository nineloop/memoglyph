import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'
import type { AppConfig, Partage, Project } from './api'
import { PORTEE_PARTAGES, PORTEE_TOUS, createProject, getConfig, getPartages, getProjects, getTrash } from './api'
import { PartageContext } from './lib/partage'
import { PartageDialog } from './components/PartageDialog'
import { ecrireUrl, lireUrl } from './lib/urlState'
import type { EtatUrl, OngletReunion } from './lib/urlState'
import { lireVueMemorisee, memoriserVue, vueDeDepart } from './lib/vuePreferee'
import { TranscribePanel } from './components/TranscribePanel'
import { LibraryPanel } from './components/LibraryPanel'
import { SearchPanel } from './components/SearchPanel'
import { AskPanel } from './components/AskPanel'
import { MaintenancePanel } from './components/MaintenancePanel'
import { VoicesPanel } from './components/VoicesPanel'
import { UsagePanel } from './components/UsagePanel'
import { Icon } from './components/Icon'
import type { IconName } from './components/Icon'
import { LangProvider, LangSelector } from './components/LangProvider'
import { AuthGate, BoutonDeconnexion, useSession } from './components/AuthGate'
import { BoutonAppareils } from './components/AppareilsPanel'
import { BoutonConfidentialite } from './components/ConfidentialitePanel'
import { BoutonComptes } from './components/ComptesPanel'
import { useLang } from './lib/langContext'
import type { MessageKey } from './lib/messages'
import { ThemeProvider } from './design/theme'
import { ThemeToggle } from './design/primitives'
import { AudioProvider } from './audio/AudioProvider'
import { MiniPlayer } from './audio/MiniPlayer'

/**
 * La portée d'ouverture : le « Non classé » du compte, ou « tous mes projets » quand il
 * n'existe pas encore (`default_project_id` vide). Une lecture ne le crée pas côté
 * serveur ; le premier envoi, vers une destination vide, si.
 */
function porteeParDefaut(config: AppConfig): string {
  return config.default_project_id || PORTEE_TOUS
}

type Tab =
  | 'transcribe' | 'library' | 'search' | 'ask' | 'voices'
  | 'usage' | 'dedup' | 'trash' | 'maintenance'

/**
 * La navigation porte DEUX choses de plus qu'une simple liste d'onglets.
 *
 * `section` — « Corbeille » et « Doublons » étaient enfouis dans Maintenance :
 * deux clics en desktop, trois en mobile, et rien dans le mot « Maintenance »
 * n'annonçait qu'une corbeille s'y trouvait. Or restaurer un document supprimé
 * est un geste d'utilisateur ordinaire, pas une opération technique. Une
 * section DONNÉES les sépare des outils de diagnostic.
 *
 * `role` — le sélecteur de portée changeait de SENS sans changer de nom :
 * destination dans Transcrire, portée de consultation ailleurs, portée
 * d'ACTION dans la corbeille. Son libellé ne dépendait que du type de projet
 * sélectionné, jamais de la vue. Le rôle vit ici parce que la vue courante y
 * est déjà : aucun état ni contexte nouveau.
 */
const NAV_ITEMS: {
  id: Tab
  key: MessageKey
  icon: IconName
  section: 'workspace' | 'data'
  role: MessageKey
}[] = [
  { id: 'transcribe', key: 'app.nav.transcribe', icon: 'upload',
    section: 'workspace', role: 'app.scope.role.destination' },
  { id: 'library', key: 'app.nav.library', icon: 'folder',
    section: 'workspace', role: 'app.scope.role.browse' },
  { id: 'search', key: 'app.nav.search', icon: 'search',
    section: 'workspace', role: 'app.scope.role.query' },
  { id: 'ask', key: 'app.nav.ask', icon: 'sparkles',
    section: 'workspace', role: 'app.scope.role.query' },
  { id: 'voices', key: 'app.nav.voices', icon: 'volume',
    section: 'workspace', role: 'app.scope.role.browse' },
  { id: 'usage', key: 'app.nav.usage', icon: 'monitor',
    section: 'data', role: 'app.scope.role.browse' },
  { id: 'dedup', key: 'app.nav.dedup', icon: 'report',
    section: 'data', role: 'app.scope.role.action' },
  { id: 'trash', key: 'app.nav.trash', icon: 'trash',
    section: 'data', role: 'app.scope.role.action' },
  { id: 'maintenance', key: 'app.nav.maintenance', icon: 'settings',
    section: 'data', role: 'app.scope.role.action' },
]

/** Vues réservées au propriétaire : absentes de la lecture seule. */
const VUES_DU_PROPRIETAIRE: ReadonlySet<Tab> = new Set<Tab>(['usage', 'dedup', 'trash', 'maintenance'])

/**
 * La langue est portée ICI et non dans `main.tsx` : le sélecteur doit provoquer
 * un vrai re-rendu de l'application, et un test qui monte `<App />` doit
 * exercer le même mécanisme que le navigateur, pas un montage dégradé.
 */
export default function App() {
  return (
    <ThemeProvider>
      <AudioProvider>
        <LangProvider>
          <AuthGate>
            <AppShell />
          </AuthGate>
        </LangProvider>
      </AudioProvider>
    </ThemeProvider>
  )
}

function AppShell() {
  const { t } = useLang()
  // La maintenance de toute la bibliothèque est réservée aux administrateurs ;
  // le serveur la refuse aux autres. Sans session connue (backend
  // injoignable), l'onglet reste : il dira lui-même la panne.
  const session = useSession()
  const estAdmin = session?.utilisateur?.admin ?? true
  const moi = session?.utilisateur?.identifiant
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [error, setError] = useState('')
  // Distinct d'`error`, qui est rendu comme « Backend injoignable : … ».
  // Un lien périmé n'est pas une panne du serveur : le dire ainsi enverrait
  // l'utilisateur chercher un problème d'infrastructure qui n'existe pas.
  const [avisPortee, setAvisPortee] = useState('')
  // L'état de navigation vient de l'URL au premier rendu.
  //
  // Sans ça : le bouton retour du navigateur quittait l'application, un
  // rechargement ramenait à Transcrire, et envoyer un lien vers une réunion
  // était impossible.
  const [etatInitial] = useState(() => lireUrl())
  // La vue d'ouverture quand l'URL n'en porte pas.
  //
  // Lue UNE fois, au montage : la relire plus tard ferait sauter la vue sous
  // les doigts de l'utilisateur dès qu'il navigue, puisqu'on la réécrit.
  const [vueMemorisee] = useState(() => lireVueMemorisee())
  // Le corpus n'est pas encore chargé : on suppose vide, donc `transcribe`.
  // Ce n'est un PROVISOIRE que si ni l'URL ni la mémoire n'ont tranché.
  const [tab, setTab] = useState<Tab>(
    () => vueDeDepart(etatInitial.vue, vueMemorisee, true))
  /**
   * « La vue d'ouverture n'est pas encore tranchée », en DEUX exemplaires.
   *
   * L'état sert à ce qui doit rester cohérent avec le `tab` du même rendu :
   * l'écriture d'URL et la mémorisation. La ref sert à ce qui DÉCIDE, et elle
   * n'est pas un doublon de confort : un effet passif encore en attente de
   * purge porte les valeurs de SON rendu. Mesuré ici — un clic de navigation
   * parti pendant l'attente était écrasé par la reprise, parce que l'effet
   * planifié avant le clic voyait toujours `true`. La ref, elle, est lue à
   * l'instant où l'effet s'exécute.
   */
  const attenteVue = useRef(!etatInitial.vue && !vueMemorisee)
  const [vueEnAttente, setVueEnAttente] = useState(attenteVue.current)
  const finirAttenteVue = useCallback(() => {
    attenteVue.current = false
    setVueEnAttente(false)
  }, [])
  const [reloadKey, setReloadKey] = useState(0)
  // `null` = configuration pas encore reçue ; `all` est la seule portée
  // transversale. Toute autre valeur est un ULID de manifeste projet.
  const [viewScope, setViewScope] = useState<string | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [quickSearch, setQuickSearch] = useState(etatInitial.q ?? '')
  const [searchRequest, setSearchRequest] = useState(
    { query: etatInitial.q ?? '', id: 0 })
  const [meetingToOpen, setMeetingToOpen] = useState<string | null>(null)
  // Réunion ouverte et onglet actif, REMONTÉS depuis les panneaux.
  //
  // Ils vivaient deux niveaux plus bas (`LibraryPanel.selected`,
  // `MeetingView.tab`). Les remonter est le prix de l'URL : un état que le
  // parent ne connaît pas ne peut pas être sérialisé.
  const [meetingOuverte, setMeetingOuverte] = useState<string | null>(
    etatInitial.reunion ?? null)
  const [ongletReunion, setOngletReunion] = useState<OngletReunion | null>(
    etatInitial.onglet ?? null)

  useEffect(() => {
    getConfig().then((value) => {
      setConfig(value)
      // Le projet de l'URL prime sur le défaut (trou nº1).
      //
      // `etatInitial.projet` était LU au montage et consommé nulle part. Un
      // lien partagé `?projet=X&reunion=Y` bornait donc la liste au projet par
      // défaut, où la réunion demandée n'est pas : le destinataire voyait un
      // écran vide sans rien pour comprendre pourquoi.
      //
      // La portée reste nulle JUSQU'ICI. `null` signale « configuration pas
      // encore reçue » et l'écriture d'URL en dépend — la remplir dès le
      // premier rendu court-circuiterait ce signal.
      setViewScope((current) => current ?? etatInitial.projet ?? porteeParDefaut(value))
    }).catch((e) => setError(String(e)))
  }, [etatInitial.projet])

  // « Liste reçue », et non « liste non vide » : un corpus VIDE est justement
  // l'un des deux cas que la vue d'ouverture doit distinguer, et une panne du
  // backend ne doit pas laisser l'application en attente pour toujours — sans
  // corpus connu, on ouvre sur Transcrire, le choix conservateur.
  const [projetsRecus, setProjetsRecus] = useState(false)
  const loadProjects = useCallback(() => {
    getProjects()
      .then(setProjects)
      .catch(() => {})
      .finally(() => setProjetsRecus(true))
  }, [])
  useEffect(() => { loadProjects() }, [loadProjects, reloadKey])

  // Ce qui est partagé avec le compte (partage v1). Best-effort : une
  // erreur laisse la liste vide, l'application reste celle du compte.
  const [recus, setRecus] = useState<Partage[]>([])
  const [recusCharges, setRecusCharges] = useState(false)
  useEffect(() => {
    let vivant = true
    getPartages()
      .then((tous) => { if (vivant) setRecus(tous.recus) })
      .catch(() => { if (vivant) setRecus([]) })
      .finally(() => { if (vivant) setRecusCharges(true) })
    return () => { vivant = false }
  }, [reloadKey])
  const projetsPartages = useMemo(() => recus.filter((p) => p.type === 'projet'), [recus])
  const reunionsPartagees = useMemo(() => recus.filter((p) => p.type === 'reunion'), [recus])
  const projetRecu = projetsPartages.find((p) => p.cible === viewScope)
  // Lecture seule : « Partagés avec moi », ou un projet reçu. Le serveur refuse
  // toute écriture de toute façon ; l'interface ne la propose pas.
  const lectureSeule = viewScope === PORTEE_PARTAGES || projetRecu !== undefined
  const partageCourant = useMemo(() => ({
    lectureSeule,
    peutPartager: (projectId: string | null | undefined) => Boolean(
      !lectureSeule && projectId && moi && projects.some((p) =>
        p.id === projectId && p.owner && p.owner.toLowerCase() === moi.toLowerCase())),
  }), [lectureSeule, moi, projects])
  const [partageOuvert, setPartageOuvert] = useState<
    { type: Partage['type']; cible: string; nom: string } | null>(null)
  // Arriver en lecture seule sur une vue du propriétaire (corbeille, doublons,
  // usage, maintenance) : on bascule sur la bibliothèque, seule vue utile.
  useEffect(() => {
    if (lectureSeule && VUES_DU_PROPRIETAIRE.has(tab)) setTab('library')
  }, [lectureSeule, tab])

  // Un projet d'URL qui n'existe pas — manifeste effacé, lien périmé, ULID mal
  // recopié — laisserait l'application sur une portée FANTÔME : aucune option
  // du sélecteur ne lui correspond, et chaque panneau interroge un projet
  // introuvable. On ne peut trancher qu'une fois la liste chargée.
  //
  // Le repli sur le défaut est DIT, pas silencieux. Ce projet a passé deux
  // jours à retirer des dégradations muettes ; en ajouter une ici, sur un lien
  // qu'on a reçu de quelqu'un, laisserait croire qu'on regarde ce qu'il a
  // voulu montrer.
  //
  // On élargit à TOUS les projets plutôt que de retomber sur le défaut.
  //
  // Un repli sur le projet par défaut ne convient pas : si le lien
  // périmé portait aussi `reunion=R`, l'URL normalisée devenait
  // `projet=<défaut>&reunion=R` — une association qu'on ne peut pas vérifier,
  // affichée comme un fait et partageable telle quelle. Nettoyer la réunion
  // serait l'autre issue ; élargir la porte est plus juste, parce qu'une
  // réunion qu'on ne sait pas classer se retrouve DANS `all`, et que
  // `projet=all&reunion=R` est vrai quel que soit son projet réel.
  //
  // Le repli est une NORMALISATION, pas une navigation : il remplace l'entrée
  // d'historique au lieu d'en empiler une que l'utilisateur n'a pas demandée.
  useEffect(() => {
    const projetUrl = etatInitial.projet
    if (!config || !projetUrl || projetUrl === 'all' || !projects.length) return
    if (projects.some((candidate) => candidate.id === projetUrl)) return
    // Un lien vers ce qui est partagé avec le compte n'est pas périmé.
    if (!recusCharges) return
    if (projetUrl === PORTEE_PARTAGES || recus.some((p) => p.cible === projetUrl)) return
    setViewScope((current) => {
      if (current !== projetUrl) return current
      setAvisPortee(t('app.scope.urlProjectMissing', { projet: projetUrl }))
      remplacerProchaineEcriture.current = true
      return 'all'
    })
  }, [config, projects, etatInitial.projet, t, recus, recusCharges])

  /**
   * La création de projet, sortie des boîtes système.
   *
   * `null` = modale fermée. L'ÉTAT PORTE LA SAISIE, et c'est tout l'intérêt :
   * `alert()` renvoyait l'échec dans une boîte qui, une fois refermée, laissait
   * l'utilisateur sans le nom qu'il venait de taper. Ici l'erreur s'affiche
   * SOUS le champ, qui a gardé sa valeur.
   */
  const [nouveauProjet, setNouveauProjet] = useState<
    { nom: string; erreur: string; enCours: boolean } | null>(null)

  function onProjectChange(value: string) {
    // L'avis décrit une portée qu'on vient de quitter : le laisser afficherait
    // un avertissement périmé au-dessus d'un écran redevenu correct.
    setAvisPortee('')
    // Une réunion reçue s'ouvre dans « Partagés avec moi », à la bibliothèque.
    if (value.startsWith('__reunion__:')) {
      setViewScope(PORTEE_PARTAGES)
      setMeetingOuverte(value.slice('__reunion__:'.length))
      navigate('library')
      return
    }
    if (value !== '__new__') { setViewScope(value); return }
    // On n'adopte PAS la sentinelle comme portée. Le `<select>` reste contrôlé
    // par `viewScope` : React réaligne l'affichage sur la portée précédente à
    // la fin de l'événement, l'URL n'est donc jamais touchée — pas même le
    // temps d'un rendu. Mesuré, pas supposé : un test tient les deux bouts.
    setNouveauProjet({ nom: '', erreur: '', enCours: false })
  }

  function fermerNouveauProjet() {
    // Une création en vol n'est pas annulable côté serveur : refermer laisserait
    // croire qu'il ne s'est rien passé.
    setNouveauProjet((etat) => (etat?.enCours ? etat : null))
  }

  async function creerProjet(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const nom = nouveauProjet?.nom.trim()
    if (!nom || nouveauProjet?.enCours) return
    setNouveauProjet((etat) => etat && { ...etat, erreur: '', enCours: true })
    try {
      // On adopte le nom NORMALISÉ par l'API : sinon le filtre courant garderait
      // la saisie brute et le transcript semblerait avoir disparu.
      const created = await createProject(nom)
      loadProjects()
      if (!created.id) throw new Error(t('app.project.noId'))
      setViewScope(created.id)
      setNouveauProjet(null)
    } catch (e) {
      // La modale RESTE ouverte, saisie intacte : c'est le seul endroit d'où
      // l'utilisateur peut corriger le nom qu'on vient de lui refuser.
      setNouveauProjet((etat) => etat && {
        ...etat,
        erreur: t('app.project.createFailed', { erreur: String(e) }),
        enCours: false,
      })
    }
  }

  const bump = () => setReloadKey((k) => k + 1)
  const activeNav = NAV_ITEMS.find((item) => item.id === tab) ?? NAV_ITEMS[0]

  // Le dock défile horizontalement sur mobile (8 destinations) : l'espace
  // courant peut être hors écran. On l'amène en vue à chaque changement de vue,
  // pour qu'aucune destination ne devienne invisible faute d'être atteinte.
  const dockActifRef = useRef<HTMLButtonElement | null>(null)
  useEffect(() => {
    dockActifRef.current?.scrollIntoView?.({ inline: 'center', block: 'nearest' })
  }, [tab])
  const currentProject = useMemo(
    () => projects.find((candidate) => candidate.id === viewScope),
    [projects, viewScope],
  )
  const isGlobalScope = viewScope === 'all'
  /**
   * Combien de documents la portée courante contient.
   *
   * `undefined` tant que les projets ne sont pas chargés — et c'est important :
   * les panneaux n'affichent alors RIEN plutôt qu'un « 0 document » qui serait
   * faux et ferait renoncer l'utilisateur avant même d'avoir cherché.
   */
  const scopeCount = useMemo(() => {
    if (!projects.length) return undefined
    if (isGlobalScope) return projects.reduce((total, item) => total + item.count, 0)
    return currentProject?.count
  }, [currentProject, isGlobalScope, projects])

  /**
   * Le compte de TOUT le corpus, et son libellé.
   *
   * Recherche et Assistant portent un bouton « chercher dans tous mes projets »
   * qui change la portée RÉELLE de la requête sans passer par ici. Sans ce
   * second compte, l'état initial continuait d'annoncer le projet — « cette
   * portée ne contient aucun document » pendant que la requête interrogeait
   * tout le corpus. C'est une composition de deux mécanismes que des tests
   * couvrant chacun séparément ne voient pas.
   */
  const scopeCountAll = useMemo(() => (
    projects.length
      ? projects.reduce((total, item) => total + item.count, 0)
      : undefined
  ), [projects])
  const scopeLabelAll = t('app.scope.allMine')
  // Nomme les fournisseurs réellement disponibles, plutôt que de supposer Ollama.
  const llmEtat = (() => {
    if (!config) return t('app.status.llmUnknown')
    // DÉRIVÉ du registre : ce bloc nommait deux moteurs en dur, si bien que MLX
    // pouvait être servi par le backend et rester invisible dans l'en-tête.
    //
    // Le libellé lisible est un CONFORT, pas une source de vérité : un
    // fournisseur sans entrée dans le catalogue s'affiche sous son identifiant.
    // La liste ne peut donc jamais MASQUER un moteur, seulement le nommer moins
    // joliment — c'est le bon sens de la dégradation.
    const joli: Record<string, string> = {
      ollama: t('app.provider.ollamaLocal'),
      mlx: t('app.provider.mlxLocal'),
      mistral: t('app.provider.mistralCloud'),
      openai: t('app.provider.openaiCloud'),
      anthropic: t('app.provider.anthropicCloud'),
      compatible: t('app.provider.compatible'),
    }
    const dispo = Object.entries(config.llm_ready || {})
      .filter(([, pret]) => pret)
      .map(([nom]) => joli[nom] || nom)
    return dispo.length
      ? t('app.status.llmAvailable', { fournisseurs: dispo.join(' + ') })
      : t('app.status.llmNone')
  })()
  // Le rôle de la vue COURANTE, plus le type du projet sélectionné.
  //
  // L'ancien calcul rendait « Projet de réception » dans les six vues, y
  // compris là où le contrôle gouverne des suppressions. Le type de projet
  // n'est pas perdu : il reste dans le texte de l'option.
  const scopeKind = t(activeNav.role)
  // Jamais un projet reçu : la transcription écrit dans un projet du compte.
  const transcriptionDestinationId = isGlobalScope || !viewScope || lectureSeule
    ? config?.default_project_id ?? ''
    : viewScope
  const transcriptionDestinationName = projects.find(
    (candidate) => candidate.id === transcriptionDestinationId,
  )?.name ?? config?.default_project ?? t('common.unclassified')
  const scopeLabel = isGlobalScope
    ? t('app.scope.allMine')
    : viewScope === PORTEE_PARTAGES
      ? t('app.scope.groupShared')
      : projetRecu
        ? t('app.scope.sharedProject', { nom: projetRecu.nom ?? '', proprietaire: projetRecu.proprietaire })
        : currentProject?.name ?? config?.default_project ?? ''

  // Compteur de corbeille, pour que le cycle archiver/restaurer soit visible
  // sans entrer dans le panneau. Best-effort : une erreur laisse le compteur à
  // zéro plutôt que de casser la navigation.
  const [trashCount, setTrashCount] = useState(0)
  useEffect(() => {
    let vivant = true
    // La corbeille est au seul propriétaire : en lecture seule, rien à compter.
    if (lectureSeule) { setTrashCount(0); return }
    getTrash(viewScope ?? '').then(
      (items) => { if (vivant) setTrashCount(items.length) },
      () => { if (vivant) setTrashCount(0) },
    )
    return () => { vivant = false }
  }, [viewScope, reloadKey, lectureSeule])

  // L'état courant, sous la forme que l'URL sait porter.
  const etatCourant: EtatUrl = useMemo(() => {
    const etat: EtatUrl = { vue: tab }
    if (viewScope) etat.projet = viewScope
    if (tab === 'library' && meetingOuverte) {
      etat.reunion = meetingOuverte
      if (ongletReunion) etat.onglet = ongletReunion
    }
    if (tab === 'search' && searchRequest.query) etat.q = searchRequest.query
    return etat
  }, [tab, viewScope, meetingOuverte, ongletReunion, searchRequest.query])

  /**
   * La prochaine écriture d'URL REMPLACE l'entrée courante au lieu d'en empiler
   * une (trous nº2 et nº3).
   *
   * Toute écriture d'URL n'est pas une navigation. Trois cas où l'utilisateur
   * n'a rien demandé et ne doit donc rien récupérer en pressant Retour :
   *
   * - **l'arrivée** : on normalise `/` en `?vue=transcribe&projet=…`. L'empiler
   *   laissait `/` derrière ; Retour y ramenait, la normalisation
   *   recommençait, et il fallait DEUX pressions de plus pour quitter
   *   l'application (mesuré : deux, pas l'infini — au troisième passage
   *   `etatCourant` est identique et l'effet ne se relance plus) ;
   * - **après un `popstate`** : ce qu'on réécrit corrige l'entrée où le
   *   navigateur vient d'arriver, il n'en crée pas une nouvelle ;
   * - **une frappe dans le champ de Recherche** : affiner une recherche
   *   n'est pas naviguer. Une entrée par mot tapé obligerait à presser Retour
   *   six fois pour sortir d'une recherche.
   *
   * Un seul drapeau pour les trois : deux mécanismes parallèles finiraient par
   * diverger sur le cas où ils se recouvrent.
   *
   * ## Ce qui n'est PAS couvert, et pourquoi
   *
   * L'armement par le `popstate` n'a pas de test : le retirer ne fait échouer
   * aucune assertion. Ce n'est pas un oubli de couverture, c'est qu'après
   * correctif le cas n'est plus constructible — toute entrée d'historique que
   * l'application a écrite est déjà canonique, donc un `popstate` y retombe sur
   * un `etatCourant` identique et l'effet sort avant d'écrire. La garde reste
   * parce qu'elle énonce l'invariant (« une écriture causée par un `popstate`
   * n'est pas une navigation ») et qu'une clé future non systématiquement
   * sérialisée rouvrirait le cas en silence. Son absence de couverture est
   * écrite ici plutôt que masquée — comme celle de la comparaison
   * anti-doublon, documentée dans `App.test.tsx`.
   */
  const remplacerProchaineEcriture = useRef(true)

  /**
   * La vue d'ouverture tranchée par le corpus, une fois la liste reçue.
   *
   * Le compte n'existe qu'après un aller-retour réseau, alors que `tab` est
   * initialisé au montage. Plutôt que de retarder tout l'écran, on rend le
   * provisoire et on corrige ici — et l'URL, elle, N'EST PAS écrite tant que
   * l'attente dure : normaliser `/` en `?vue=transcribe` pour le remplacer une
   * frame plus tard laisserait une entrée d'historique vers une vue que
   * personne n'a demandée.
   */
  useEffect(() => {
    // `scopeCountAll` vaut `undefined` tant qu'AUCUN projet n'est connu — liste
    // non chargée, ou backend injoignable, dont le `.catch` est muet. Le
    // traiter comme « corpus vide » ferait deux dégâts : ouvrir Transcrire sur
    // une supposition, et la MÉMORISER. La reprise cesserait alors de marcher
    // durablement, sans que rien ne le dise, à cause d'une panne d'un instant.
    //
    // On ne tranche donc pas. Le provisoire reste affiché, rien n'est écrit, et
    // le premier clic de l'utilisateur met fin à l'attente par `navigate`.
    if (!attenteVue.current || !projetsRecus || scopeCountAll === undefined) return
    finirAttenteVue()
    setTab(vueDeDepart(etatInitial.vue, vueMemorisee, scopeCountAll === 0))
  }, [projetsRecus, scopeCountAll, etatInitial.vue, vueMemorisee, finirAttenteVue])

  /** La reprise ne vaut que pour les vues de travail — `memoriserVue` filtre. */
  useEffect(() => {
    // Pendant l'attente, `tab` n'est qu'un provisoire : le mémoriser figerait
    // Transcrire comme préférence sans que l'utilisateur ait rien choisi.
    // L'état, ici aussi : il doit rester cohérent avec le `tab` du même rendu.
    if (!vueEnAttente) memoriserVue(tab)
  }, [tab, vueEnAttente])

  // ÉCRITURE — on compare avant de pousser.
  //
  // `StrictMode` invoque les effets deux fois en développement : sans cette
  // comparaison, chaque navigation empilerait DEUX entrées d'historique et le
  // bouton retour paraîtrait coincé. La comparaison porte sur la chaîne
  // sérialisée, d'où l'ordre fixe des clés dans `ecrireUrl`.
  useEffect(() => {
    if (!config) return  // avant le chargement, `viewScope` est encore nul
    // La vue d'ouverture n'est pas tranchée : on n'écrit RIEN. L'état, et non
    // la ref — dans le rendu qui la tranche, `tab` vaut encore le provisoire,
    // et écrire ici coûterait une entrée d'historique vers une vue que
    // personne n'a demandée (mesuré : elle absorbait un Retour).
    if (vueEnAttente) return
    // Lu ET consommé AVANT le retour anticipé. Un drapeau qui survivrait à une
    // écriture sans effet ferait passer la navigation suivante — une vraie,
    // celle-là — pour une normalisation, et lui volerait son entrée.
    const remplacer = remplacerProchaineEcriture.current
    remplacerProchaineEcriture.current = false
    const cible = ecrireUrl(etatCourant)
    if (cible === location.search || cible === location.pathname + location.search) return
    if (remplacer) history.replaceState(null, '', cible)
    else history.pushState(null, '', cible)
  }, [config, etatCourant, vueEnAttente])

  // RETOUR ARRIÈRE — on relit l'URL, on ne devine pas.
  useEffect(() => {
    const surRetour = () => {
      const etat = lireUrl()
      remplacerProchaineEcriture.current = true
      setAvisPortee('')
      finirAttenteVue()
      // Toute entrée d'historique écrite par l'application porte sa `vue` :
      // une entrée SANS vue n'a pu être que tapée à la main, et `transcribe`
      // y vaut mieux qu'un écran qui bougerait après coup.
      setTab(etat.vue ?? 'transcribe')
      setViewScope(etat.projet ?? null)
      setMeetingOuverte(etat.reunion ?? null)
      setOngletReunion(etat.onglet ?? null)
      // La requête passe par `searchRequest.id` et NON par `initialQuery` seul :
      // `SearchPanel` ne lit cette prop qu'au montage, c'est le changement de
      // `key` qui le remonte. Sans incrémenter l'id, un retour arrière
      // changerait l'URL sans changer l'écran.
      setSearchRequest((precedent) =>
        precedent.query === (etat.q ?? '')
          ? precedent
          : { query: etat.q ?? '', id: precedent.id + 1 })
      setQuickSearch(etat.q ?? '')
    }
    addEventListener('popstate', surRetour)
    return () => removeEventListener('popstate', surRetour)
  }, [finirAttenteVue])

  /**
   * La requête tapée dans le champ principal de Recherche remonte ici
   * (trou nº3).
   *
   * `SearchPanel` gardait sa saisie dans son état local : elle n'atteignait
   * jamais l'URL, disparaissait au rechargement et n'était pas partageable. Et
   * c'est le chemin DOMINANT depuis que la barre haute est masquée sur cette
   * vue.
   *
   * L'identifiant ne bouge PAS. C'est lui qui compose la `key` de
   * `SearchPanel` : l'incrémenter ici remonterait le composant à chaque mot
   * tapé — champ défocalisé, requête relancée, résultats clignotants.
   */
  const surRequeteRecherche = useCallback((query: string) => {
    setSearchRequest((courant) => {
      if (courant.query === query) return courant
      remplacerProchaineEcriture.current = true
      return { query, id: courant.id }
    })
    // La barre haute reste alignée : en quittant Recherche, elle afficherait
    // sinon une requête que l'utilisateur a déjà remplacée.
    setQuickSearch(query)
  }, [])

  function navigate(next: Tab) {
    // Un choix explicite met fin à l'attente : sans ça, la liste de projets
    // arrivant après le clic renverrait l'utilisateur là où LUI n'a pas voulu.
    finirAttenteVue()
    setTab(next)
  }

  function submitQuickSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const query = quickSearch.trim()
    if (!query) return
    setSearchRequest((current) => ({ query, id: current.id + 1 }))
    navigate('search')
  }

  return (
    <div className="app">
      <div className="app-frame">
        <header className="contextbar">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">
              <img className="brand-mark-clair" src="/brand/memoglyph-symbole.svg" alt="" />
              <img className="brand-mark-sombre" src="/brand/memoglyph-symbole-sombre.svg" alt="" />
            </span>
            <span>Memoglyph</span>
          </div>

          <div className="context-heading">
            <span>{t(activeNav.key)}</span>
            <strong>{isGlobalScope ? t('app.scope.allProjects')
              : lectureSeule ? scopeLabel : currentProject?.name ?? t('app.loading')}</strong>
            {lectureSeule && <span className="scope-kind readonly">{t('app.scope.readOnly')}</span>}
          </div>

{/* La barre haute est l'entrée UNIVERSELLE — elle est rendue sur toutes
              les vues, et c'est ce qui permet de lancer une recherche depuis
              n'importe où. Mais sur la vue Recherche elle DOUBLE un champ meilleur
              qu'elle, à vingt pixels : autofocus, placeholder par mode, saisie au
              fil de la frappe. On la retire là, et là seulement. */}
          {tab !== 'search' && (
                      <form className="global-search" role="search" onSubmit={submitQuickSearch}>
              <button type="submit" aria-label={t('app.search.submit')}><Icon name="search" /></button>
              <input type="search" value={quickSearch} onChange={(event) => setQuickSearch(event.target.value)}
                     aria-label={t('app.search.label')} placeholder={t('app.search.placeholder')} />
            </form>
          )}

          <div className="scope-picker">
            <span className={`scope-kind ${isGlobalScope ? 'global' : ''}`}>{scopeKind}</span>
            <select
              className="project-select"
              value={viewScope ?? ''}
              onChange={(e) => onProjectChange(e.target.value)}
              title={t('app.scope.hint')}
              aria-label={t('app.scope.current')}
            >
              <option value="all">{t('app.scope.allProjectsOption')}</option>
              <optgroup label={t('app.scope.groupProjects')}>
                {projects.filter((project) => project.id).map((project) => {
                  const nom = project.owner === null
                    ? t('app.scope.ownerless', { nom: project.name })
                    : project.name
                  return (
                    <option key={project.id!} value={project.id!}>
                      {project.kind === 'inbox'
                        ? t('app.scope.inboxOption', { nom, compte: project.count })
                        : t('app.scope.projectOption', { nom, compte: project.count })}
                    </option>
                  )
                })}
              </optgroup>
              {recus.length > 0 && (
                <optgroup label={t('app.scope.groupShared')}>
                  <option value={PORTEE_PARTAGES}>{t('app.scope.sharedAll')}</option>
                  {projetsPartages.map((p) => (
                    <option key={p.id} value={p.cible}>
                      {t('app.scope.sharedProject', { nom: p.nom ?? p.cible, proprietaire: p.proprietaire })}
                    </option>
                  ))}
                  {reunionsPartagees.map((p) => (
                    <option key={p.id} value={`__reunion__:${p.cible}`}>
                      {t('app.scope.sharedMeeting', { nom: p.nom ?? p.cible, proprietaire: p.proprietaire })}
                    </option>
                  ))}
                </optgroup>
              )}
              <optgroup label={t('app.scope.groupActions')}>
                <option value="__new__">{t('app.scope.newProject')}</option>
              </optgroup>
            </select>
            {/* Partager un projet se décide en le regardant : dans la Bibliothèque, pas depuis
                Transcrire, où le sélecteur ne choisit qu'une destination. La réception
                « Non classé » ne se partage pas ; un projet vide, si : le partage vaut
                aussi pour ses réunions à venir. */}
            {tab === 'library' && currentProject?.id && currentProject.kind !== 'inbox'
              && partageCourant.peutPartager(currentProject.id) && (
              <button type="button" className="chip"
                      onClick={() => setPartageOuvert({
                        type: 'projet', cible: currentProject.id!, nom: currentProject.name })}>
                {t('share.button')}
              </button>
            )}
          </div>

          <ThemeToggle />

          <LangSelector />

          <BoutonAppareils />

          <BoutonConfidentialite />

          <BoutonComptes />

          <BoutonDeconnexion />

          {config && (
            <div className="status-dots" aria-label={t('app.status.services')}>
              {/* Le libellé nommait « Ollama » en dur alors que l'état vient
                  d'un OU entre les deux fournisseurs : un utilisateur Mistral
                  voyait une pastille verte attribuée au mauvais moteur. */}
              <span className={config.llm_available ? 'dot ok' : 'dot off'} title={llmEtat}>LLM</span>
              <span className={config.diarization_available ? 'dot ok' : 'dot off'} title={t('app.status.diarization')}>Diar</span>
            </div>
          )}
        </header>

        <PartageContext.Provider value={partageCourant}>
        <main className="app-main">
        {error && <div className="error banner">{t('app.backendDown', { erreur: error })}</div>}
        {avisPortee && (
          <div className="warn banner" role="status">
            <Icon name="alert" /> {avisPortee}
          </div>
        )}
        {!config && !error && <p className="muted center">{t('app.connecting')}</p>}
        {config && tab === 'transcribe' && (
          <TranscribePanel
            config={config}
            destinationProjectId={transcriptionDestinationId}
            destinationProjectName={transcriptionDestinationName}
            onCompleted={bump}
            onViewMeeting={(documentName, projectId) => {
              setViewScope(projectId)
              setMeetingToOpen(documentName)
              navigate('library')
            }}
          />
        )}
        {config && tab === 'library' && (
          <LibraryPanel key={viewScope} reloadKey={reloadKey} viewScope={viewScope ?? porteeParDefaut(config)} projects={projects} onChanged={bump}
            openDocumentName={meetingToOpen}
            onInitialMeetingOpened={() => setMeetingToOpen(null)}
            openMeetingId={meetingOuverte}
            onMeetingChanged={(id) => {
              setMeetingOuverte(id)
              // Fermer une réunion referme aussi son onglet : le garder
              // produirait une URL avec `onglet` sans `reunion`, que
              // `ecrireUrl` omet — l'état interne et l'URL divergeraient.
              if (!id) setOngletReunion(null)
            }}
            meetingTab={ongletReunion}
            onMeetingTabChange={(onglet) => setOngletReunion(onglet as OngletReunion)}
            onSelectProject={onProjectChange}
            onVoirDoublons={() => navigate('dedup')} onTranscrire={() => navigate('transcribe')} />
        )}
        {config && tab === 'search' && (
          <SearchPanel key={`${viewScope}:${searchRequest.id}`} viewScope={viewScope ?? porteeParDefaut(config)}
                       scopeLabel={scopeLabel}
                       scopeCount={scopeCount}
                       scopeCountAll={scopeCountAll}
                       scopeLabelAll={scopeLabelAll}
                       initialQuery={searchRequest.query}
                       onQueryChange={surRequeteRecherche} />
        )}
        {config && tab === 'ask' && (
          <AskPanel
            key={viewScope}
            viewScope={viewScope ?? porteeParDefaut(config)}
            scopeLabel={scopeLabel}
            scopeCount={scopeCount}
            scopeCountAll={scopeCountAll}
            scopeLabelAll={scopeLabelAll}
            llmAvailable={config.llm_available}
          />
        )}
        {config && tab === 'voices' && (
          <VoicesPanel
            key={viewScope}
            viewScope={viewScope ?? porteeParDefaut(config)}
            scopeLabel={scopeLabel}
          />
        )}
        {/* Un seul panneau, trois portes d'entrée.
            `MaintenancePanel` porte déjà un état `section` avec « Doublons » et
            « Corbeille » : les séparer en composants distincts aurait dupliqué
            le chargement et la portée pour rien. Ce qui manquait n'était pas un
            écran, c'était un CHEMIN. */}
        {config && tab === 'usage' && (
          <UsagePanel
            key={viewScope}
            viewScope={viewScope ?? porteeParDefaut(config)}
            scopeLabel={scopeLabel}
          />
        )}
        {config && (tab === 'dedup' || tab === 'trash' || tab === 'maintenance') && (
          <MaintenancePanel
            key={`${viewScope}:${tab}`}
            admin={estAdmin}
            viewScope={viewScope ?? porteeParDefaut(config)}
            scopeLabel={scopeLabel}
            initialSection={
              tab === 'trash' ? 'trash' : tab === 'dedup' ? 'duplicates' : 'status'
            }
            onChanged={bump}
          />
        )}
        </main>
        </PartageContext.Provider>
      </div>

      {partageOuvert && (
        <PartageDialog type={partageOuvert.type} cible={partageOuvert.cible} nom={partageOuvert.nom}
                       onClose={() => setPartageOuvert(null)} />
      )}

      {/* Même patron que la modale de fusion de `VoicesPanel` : `modal-backdrop`
          + `merge-dialog`, fermeture au clic sur le fond, bouton de fermeture
          étiqueté. Pas de composant partagé extrait — les deux modales ne
          partagent aujourd'hui que la coquille, et une abstraction à deux cas
          coûterait plus qu'elle ne rend. Si une troisième arrive, c'est là
          qu'il faudra la sortir. */}
      {nouveauProjet && (
        <div className="modal-backdrop"
             onMouseDown={(event) => {
               if (event.target === event.currentTarget) fermerNouveauProjet()
             }}
             onKeyDown={(event) => { if (event.key === 'Escape') fermerNouveauProjet() }}>
          {/* Un vrai `<form>` : Entrée valide, et le bouton désactivé sur un nom
              blanc bloque AUSSI la touche Entrée — ce qu'un `<div>` + `onClick`
              n'aurait pas fait. */}
          <form className="merge-dialog project-dialog" role="dialog" aria-modal="true"
                aria-labelledby="nouveau-projet-titre" onSubmit={creerProjet}>
            <div className="merge-dialog-head">
              <div>
                <span className="action-scope">{t('app.project.dialogScope')}</span>
                <h2 id="nouveau-projet-titre">{t('app.project.dialogTitle')}</h2>
              </div>
              <button type="button" className="icon-btn" aria-label={t('app.project.dialogClose')}
                      onClick={fermerNouveauProjet} disabled={nouveauProjet.enCours}>×</button>
            </div>

            <label className="field" htmlFor="nouveau-projet-nom">
              <span>{t('app.project.nameLabel')}</span>
              <input id="nouveau-projet-nom" type="text" autoFocus
                     value={nouveauProjet.nom}
                     placeholder={t('app.project.namePlaceholder')}
                     disabled={nouveauProjet.enCours}
                     onChange={(event) => setNouveauProjet(
                       (etat) => etat && { ...etat, nom: event.target.value })} />
            </label>

            {nouveauProjet.erreur && (
              <p className="error" role="alert"><Icon name="error" /> {nouveauProjet.erreur}</p>
            )}

            <div className="dialog-actions">
              <button type="button" className="btn" onClick={fermerNouveauProjet}
                      disabled={nouveauProjet.enCours}>{t('app.project.cancel')}</button>
              <button type="submit" className="btn accent"
                      disabled={!nouveauProjet.nom.trim() || nouveauProjet.enCours}>
                {nouveauProjet.enCours ? t('app.project.creating') : t('app.project.create')}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Navigation PRIMAIRE = dock flottant (signature de l'interface),
          un seul landmark. Les intitulés de groupe restent dans le DOM pour les
          lecteurs d'écran ; le compteur de corbeille reste discret. */}
      <nav className="dock" aria-label={t('app.nav.main')}>
        {(['workspace', 'data'] as const).map((groupe) => (
          <Fragment key={groupe}>
            <span className="nav-caption">
              {t(groupe === 'workspace' ? 'app.nav.workspace' : 'app.nav.data')}
            </span>
            {NAV_ITEMS.filter((item) => item.section === groupe
                              && (estAdmin || item.id !== 'maintenance')
                              && !(lectureSeule && VUES_DU_PROPRIETAIRE.has(item.id))).map((item) => (
              <button key={item.id} type="button"
                      ref={tab === item.id ? dockActifRef : undefined}
                      aria-current={tab === item.id ? 'page' : undefined}
                      className={`dock-item ${tab === item.id ? 'on' : ''}`}
                      onClick={() => navigate(item.id)}>
                <span className="dock-glyph"><Icon name={item.icon} /></span>
                <span className="dock-label">{t(item.key)}</span>
                {item.id === 'trash' && trashCount > 0 && (
                  <span className="nav-badge">{trashCount}</span>
                )}
              </button>
            ))}
          </Fragment>
        ))}
      </nav>

      <MiniPlayer />
    </div>
  )
}
