import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'
import type { AppConfig, Partage, Project } from './api'
import { PORTEE_PARTAGES, PORTEE_TOUS, createProject, getConfig, getPartages, getProjects, getTrash } from './api'
import { PartageContext } from './lib/partage'
import { destinationParDefaut, ecrireDestination, ecrirePanneauReplie, lireDestination, lirePanneauReplie } from './lib/navigationEtat'
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
import { LangProvider } from './components/LangProvider'
import { AuthGate, useSession } from './components/AuthGate'
import { BarreHaute } from './components/BarreHaute'
import { ProjetsPanel, ProjetsTiroir } from './components/ProjetsPanel'
import { BoutonPlus } from './components/MobileNav'
import { IndicateurTraitements } from './components/IndicateurTraitements'
import { PanneauTraitements } from './components/PanneauTraitements'
import { useTraitements } from './lib/useTraitements'
import { TraitementsContext } from './lib/traitementsContext'
import { libelleProjet } from './lib/libelleProjet'
import { useLang } from './lib/langContext'
import type { MessageKey } from './lib/messages'
import { ThemeProvider } from './design/theme'
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
 * `section` — « Corbeille » et « Doublons » étaient enfouis dans Maintenance : deux clics en
 * desktop, trois en mobile, et rien dans le mot « Maintenance » n'annonçait qu'une corbeille
 * s'y trouvait. Or restaurer un document supprimé est un geste d'utilisateur ordinaire, pas une
 * opération technique : une section DONNÉES les sépare des outils de diagnostic.
 *
 * Le « rôle » du sélecteur de portée (destination, consultation, action) a disparu avec le
 * sélecteur : la destination se choisit dans Transcrire, la consultation dans le panneau.
 */
const NAV_ITEMS: {
  id: Tab
  key: MessageKey
  icon: IconName
  section: 'workspace' | 'data'
}[] = [
  { id: 'transcribe', key: 'app.nav.transcribe', icon: 'upload',
    section: 'workspace' },
  { id: 'library', key: 'app.nav.library', icon: 'folder',
    section: 'workspace' },
  { id: 'search', key: 'app.nav.search', icon: 'search',
    section: 'workspace' },
  { id: 'ask', key: 'app.nav.ask', icon: 'sparkles',
    section: 'workspace' },
  { id: 'voices', key: 'app.nav.voices', icon: 'volume',
    section: 'workspace' },
  { id: 'usage', key: 'app.nav.usage', icon: 'monitor',
    section: 'data' },
  { id: 'dedup', key: 'app.nav.dedup', icon: 'report',
    section: 'data' },
  { id: 'trash', key: 'app.nav.trash', icon: 'trash',
    section: 'data' },
  { id: 'maintenance', key: 'app.nav.maintenance', icon: 'settings',
    section: 'data' },
]

/** Les quatre entrées du dock mobile ; les autres s'ouvrent sous « Plus » (au bureau, tout reste visible). */
const VUES_DU_DOCK_MOBILE: ReadonlySet<Tab> = new Set<Tab>(['transcribe', 'library', 'search', 'ask'])

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
  // Trois responsabilités que `viewScope` mélangeait (retour 15) : ce qu'on CONSULTE (`viewScope`),
  // où se range la prochaine transcription (`destinationChoisie`) et l'état du panneau. Changer
  // l'un ne modifie jamais l'autre : la destination ne dépend plus du projet consulté.
  const [panneauReplie, setPanneauReplie] = useState(() => lirePanneauReplie(moi ?? ''))
  const [tiroirOuvert, setTiroirOuvert] = useState(false)
  const [destinationMemorisee] = useState(() => lireDestination(moi ?? ''))
  const [destinationChoisie, setDestinationChoisie] = useState<string | null>(destinationMemorisee)
  // Les traitements du compte, relevés à leur rythme (3 s actif, 15 s au repos, suspendus onglet masqué).
  // Le panneau s'ouvre par `?traitements=1` : un lien ou un rechargement le retrouve.
  const { liste: traitements, rafraichir: rafraichirTraitements } = useTraitements()
  const [traitementsOuvert, setTraitementsOuvert] = useState(etatInitial.traitements ?? false)
  const [quickSearch, setQuickSearch] = useState(etatInitial.q ?? '')
  const [searchRequest, setSearchRequest] = useState(
    { query: etatInitial.q ?? '', id: 0, partout: false })
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
  const [donnes, setDonnes] = useState<Partage[]>([])
  const [recusCharges, setRecusCharges] = useState(false)
  useEffect(() => {
    let vivant = true
    getPartages()
      .then((tous) => { if (vivant) { setRecus(tous.recus); setDonnes(tous.donnes) } })
      .catch(() => { if (vivant) { setRecus([]); setDonnes([]) } })
      .finally(() => { if (vivant) setRecusCharges(true) })
    return () => { vivant = false }
  }, [reloadKey])
  const projetsPartages = useMemo(() => recus.filter((p) => p.type === 'projet'), [recus])
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

  /**
   * Consulter un projet : le panneau, le fil d'Ariane et la galerie passent tous par ici.
   *
   * L'avis décrit une portée qu'on vient de quitter ; le laisser afficherait un avertissement
   * périmé au-dessus d'un écran redevenu correct. Un choix explicite met aussi fin à l'attente de
   * la vue d'ouverture, sans quoi la liste de projets arrivant après le clic renverrait ailleurs.
   */
  function consulter(cible: string) {
    setAvisPortee('')
    finirAttenteVue()
    setViewScope(cible)
  }

  /** « Changer dans la Bibliothèque » : on y consulte un autre projet, ici on ne fait que le lire. */
  const allerBibliotheque = () => navigate('library')

  /**
   * Ouvrir ou fermer le panneau des traitements. Ce n'est pas une navigation : l'URL est REMPLACÉE
   * (`?traitements=1`), pas empilée, pour que Retour quitte l'écran et non le panneau.
   */
  function basculerTraitements(ouvert: boolean) {
    remplacerProchaineEcriture.current = true
    setTraitementsOuvert(ouvert)
  }

  /** « Ouvrir la réunion » d'un traitement terminé : sa réunion, dans son projet, et le panneau se ferme. */
  function ouvrirReunionDuTraitement(reunion: { name: string; project_id?: string }) {
    if (reunion.project_id) consulter(reunion.project_id)
    setMeetingToOpen(reunion.name)
    // C'est une vraie NAVIGATION : l'entrée d'historique est empilée (Retour ramène où l'on était). On ne
    // passe donc pas par `basculerTraitements`, qui REMPLACE l'entrée courante.
    setTraitementsOuvert(false)
    navigate('library')
  }

  function ouvrirNouveauProjet() {
    setNouveauProjet({ nom: '', erreur: '', enCours: false })
  }

  function basculerPanneau(replie: boolean) {
    setPanneauReplie(replie)
    ecrirePanneauReplie(moi ?? '', replie)
  }

  function choisirDestination(id: string) {
    setDestinationChoisie(id)
    ecrireDestination(moi ?? '', id)
  }

  /** Crée un projet depuis « Ranger dans » : il devient la destination, JAMAIS le projet consulté. */
  async function creerProjetDestination(nom: string): Promise<Project> {
    const cree = await createProject(nom)
    loadProjects()
    return cree
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

  // Le dock défile horizontalement sur mobile (8 destinations) : l'espace
  // courant peut être hors écran. On l'amène en vue à chaque changement de vue,
  // pour qu'aucune destination ne devienne invisible faute d'être atteinte.
  const entreesDuDock = NAV_ITEMS.filter((item) => (estAdmin || item.id !== 'maintenance')
    && !(lectureSeule && VUES_DU_PROPRIETAIRE.has(item.id)))
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
  // Jamais un projet reçu : la transcription écrit dans un projet du compte.
  const transcriptionDestinationId = destinationParDefaut(
    projects, destinationChoisie, config?.default_project_id ?? '')
  const destinationProposee = destinationMemorisee !== null
    && destinationChoisie === destinationMemorisee
    && transcriptionDestinationId === destinationMemorisee
  // Partager se décide en regardant le projet : l'en-tête de la Bibliothèque porte le bouton.
  // La réception « Non classé » ne se partage pas ; un projet vide, si (le partage vaut aussi
  // pour ses réunions à venir).
  const projetPartageable = Boolean(currentProject?.id && currentProject.kind !== 'inbox'
    && partageCourant.peutPartager(currentProject.id))
  const partagesDuProjet = donnes
    .filter((p) => p.type === 'projet' && p.cible === currentProject?.id).map((p) => p.invite)
  const scopeLabel = isGlobalScope
    ? t('app.scope.allMine')
    : viewScope === PORTEE_PARTAGES
      ? t('app.scope.groupShared')
      : projetRecu
        ? t('app.scope.sharedProject', { nom: projetRecu.nom ?? '', proprietaire: projetRecu.proprietaire })
        : libelleProjet(currentProject?.name ?? config?.default_project ?? '', t, currentProject?.kind)

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
    if (traitementsOuvert) etat.traitements = true
    return etat
  }, [tab, viewScope, meetingOuverte, ongletReunion, searchRequest.query, traitementsOuvert])

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
      setTraitementsOuvert(etat.traitements ?? false)
      // La requête passe par `searchRequest.id` et NON par `initialQuery` seul :
      // `SearchPanel` ne lit cette prop qu'au montage, c'est le changement de
      // `key` qui le remonte. Sans incrémenter l'id, un retour arrière
      // changerait l'URL sans changer l'écran.
      setSearchRequest((precedent) =>
        precedent.query === (etat.q ?? '')
          ? precedent
          : { query: etat.q ?? '', id: precedent.id + 1, partout: false })
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
      return { ...courant, query }
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

  function submitQuickSearch(saisie: string) {
    const query = saisie.trim()
    if (!query) return
    // La recherche de la barre haute est globale : « toutes les réunions ».
    setSearchRequest((current) => ({ query, id: current.id + 1, partout: true }))
    navigate('search')
  }

  return (
    <div className="app">
      <div className="app-frame">
        {/* La barre haute est l'entrée UNIVERSELLE — rendue sur toutes les vues, elle permet de
            lancer une recherche depuis n'importe où ; sur la vue Recherche elle DOUBLERAIT un
            champ meilleur qu'elle (autofocus, placeholder par mode, saisie au fil de la frappe),
            on la retire là, et là seulement. */}
        <BarreHaute
          llm={config?.llm_available ?? false}
          diarisation={config?.diarization_available ?? false}
          servicesInconnus={!config}
          llmEtat={llmEtat}
          recherche={quickSearch}
          surChangerRecherche={setQuickSearch}
          surRecherche={(saisie) => submitQuickSearch(saisie)}
          surAccueil={() => navigate('transcribe')}
          masquerRecherche={tab === 'search'}
          indicateur={
            <IndicateurTraitements liste={traitements} ouvert={traitementsOuvert}
                                   surBasculer={() => basculerTraitements(!traitementsOuvert)} />
          }
        />
        {traitementsOuvert && (
          <PanneauTraitements liste={traitements} surFermer={() => basculerTraitements(false)}
                              surOuvrirReunion={ouvrirReunionDuTraitement} rafraichir={rafraichirTraitements} />
        )}

        <TraitementsContext.Provider value={{ liste: traitements, rafraichir: rafraichirTraitements, suivre: () => basculerTraitements(true) }}>
        <PartageContext.Provider value={partageCourant}>
        {/* La Bibliothèque et ses réunions prennent toute la largeur (le transcript surtout) ; les autres écrans
            gardent la largeur pour laquelle ils sont dessinés. */}
        <main className={`app-main ${tab === 'library' ? 'app-main--bibliotheque' : ''}`}>
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
            projects={projects}
            destinationProjectId={transcriptionDestinationId}
            onChooseDestination={choisirDestination}
            onCreateDestination={creerProjetDestination}
            destinationSuggested={destinationProposee}
            traitements={traitements}
            onSubmitted={rafraichirTraitements}
            onSuivre={() => basculerTraitements(true)}
            onCompleted={bump}
            onViewMeeting={(documentName, projectId) => {
              setViewScope(projectId)
              setMeetingToOpen(documentName)
              navigate('library')
            }}
          />
        )}
        {config && tab === 'library' && (
          <div className={`bibliotheque-mise ${panneauReplie ? 'replie' : ''}`}>
            <ProjetsPanel projets={projects} recus={recus} consulte={viewScope ?? porteeParDefaut(config)}
                          replie={panneauReplie} surBasculer={basculerPanneau}
                          surConsulter={consulter} surNouveau={ouvrirNouveauProjet} />
            <div className="bibliotheque-contenu">
              {/* Sur petit écran le panneau devient un tiroir ouvert par ce bouton. */}
              <button type="button" className="btn tiroir-bouton" aria-haspopup="dialog"
                      aria-expanded={tiroirOuvert} onClick={() => setTiroirOuvert(true)}>
                <Icon name="menu" /> {t('nav.drawer.open')}
              </button>
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
                  onSelectProject={consulter}
            titre={scopeLabel}
            partageAvec={projetPartageable ? partagesDuProjet : undefined}
            onPartager={projetPartageable && currentProject?.id ? () => setPartageOuvert({
              type: 'projet', cible: currentProject.id!, nom: currentProject.name }) : undefined}
                  onVoirDoublons={() => navigate('dedup')} onTranscrire={() => navigate('transcribe')} />
            </div>
            <ProjetsTiroir ouvert={tiroirOuvert} surFermer={() => setTiroirOuvert(false)}
                           projets={projects} recus={recus} consulte={viewScope ?? porteeParDefaut(config)}
                           surConsulter={consulter}
                           surNouveau={() => { setTiroirOuvert(false); ouvrirNouveauProjet() }} />
          </div>
        )}
        {config && tab === 'search' && (
          <SearchPanel key={`${viewScope}:${searchRequest.id}`} viewScope={viewScope ?? porteeParDefaut(config)}
                       scopeLabel={scopeLabel}
                       scopeCount={scopeCount}
                       scopeCountAll={scopeCountAll}
                       scopeLabelAll={scopeLabelAll}
                       initialQuery={searchRequest.query}
                       initialEverywhere={searchRequest.partout}
                       onChangerPortee={allerBibliotheque}
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
            onChangerPortee={allerBibliotheque}
          />
        )}
        {config && tab === 'voices' && (
          <VoicesPanel
            key={viewScope}
            viewScope={viewScope ?? porteeParDefaut(config)}
            scopeLabel={scopeLabel}
            onChangerPortee={allerBibliotheque}
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
            onChangerPortee={allerBibliotheque}
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
            onChangerPortee={allerBibliotheque}
          />
        )}
        </main>
        </PartageContext.Provider>
        </TraitementsContext.Provider>
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
            {entreesDuDock.filter((item) => item.section === groupe).map((item) => (
              <button key={item.id} type="button"
                      ref={tab === item.id ? dockActifRef : undefined}
                      aria-current={tab === item.id ? 'page' : undefined}
                      className={`dock-item ${tab === item.id ? 'on' : ''} ${VUES_DU_DOCK_MOBILE.has(item.id) ? '' : 'dock-secondaire'}`}
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
        <BoutonPlus courant={tab} surChoisir={(id) => navigate(id as Tab)}
                    entrees={entreesDuDock.filter((item) => !VUES_DU_DOCK_MOBILE.has(item.id)).map((item) => ({
                      id: item.id, label: t(item.key), icon: item.icon,
                      badge: item.id === 'trash' ? trashCount : undefined }))} />
      </nav>

      <MiniPlayer />
    </div>
  )
}
