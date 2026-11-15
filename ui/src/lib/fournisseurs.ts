import type { AppConfig, EtatDuFournisseur, LieuFournisseur } from '../api'
import type { Traduire } from './i18n'
import type { MessageKey } from './messages'

/**
 * LA table des libellés de fournisseurs : un identifiant du registre → la clé de message de son nom. Elle remplace les tables
 * qui vivaient dans chaque composant. Un fournisseur sans libellé garde son identifiant à l'écran (`libelleDe`).
 */
export const LIBELLES: Record<string, MessageKey> = {
  mlx: 'app.provider.mlxLocal',
  mistral: 'app.provider.mistralCloud',
  openai: 'app.provider.openaiCloud',
  anthropic: 'app.provider.anthropicCloud',
  compatible: 'app.provider.compatible',
  openrouter: 'app.provider.openrouter',
  groq: 'app.provider.groq',
}

/**
 * Le fournisseur du modèle local de cette installation, quand l'écran n'en sait pas plus (serveur muet, aucun réglage). Remplace le
 * repli que le magasin du navigateur portait : le choix, lui, vit dans les réglages du COMPTE.
 */
export const FOURNISSEUR_LOCAL_DE_REPLI = 'mlx'

export interface FournisseurConnu {
  id: string
  lieu: LieuFournisseur
  etat: EtatDuFournisseur
}

type ConfigFournisseurs = Pick<AppConfig, 'llm_etats' | 'llm_providers' | 'llm_ready' | 'local_providers' | 'private_cloud_providers'>

/**
 * Tous les fournisseurs connus du serveur, avec où ils tournent et s'ils sont prêts. Le modèle local d'abord, puis les fournisseurs
 * en ligne, puis les « cloud privé » ; l'ordre du registre sinon. Un serveur qui ne dirait pas `llm_etats` (ancien) : on le déduit
 * de `llm_ready` et des listes locales, sans rien inventer.
 */
export function fournisseursConnus(config: ConfigFournisseurs): FournisseurConnu[] {
  const etats = config.llm_etats
  const locaux = config.local_providers ?? ['mlx']
  const prives = config.private_cloud_providers ?? []
  const liste: FournisseurConnu[] = etats
    ? Object.entries(etats).map(([id, e]) => ({ id, lieu: e.lieu, etat: e.etat }))
    : Object.entries(config.llm_ready ?? {}).filter(([id]) => id !== 'cloud' && (config.llm_providers ?? []).includes(id))
        .map(([id, pret]) => ({
          id, etat: pret ? 'pret' : 'non_configure',
          lieu: locaux.includes(id) ? 'local' : prives.includes(id) ? 'cloud_prive' : 'en_ligne',
        }))
  const rang = (f: FournisseurConnu) => (f.lieu === 'local' ? 0 : f.lieu === 'en_ligne' ? 1 : 2)
  return liste.map((f, i) => ({ f, i })).sort((a, b) => rang(a.f) - rang(b.f) || a.i - b.i).map(({ f }) => f)
}

/** Un fournisseur se choisit s'il est prêt (ou « modèle gratuit ») : jamais un non configuré. */
export const estPret = (f: FournisseurConnu) => f.etat === 'pret' || f.etat === 'gratuit'

/**
 * La règle d'envoi, REFLÉTÉE à l'écran (le serveur garde le dernier mot) : un document ultra-confidentiel ne part pas en ligne
 * sans l'autorisation du propriétaire, et un modèle gratuit ne le reçoit JAMAIS, autorisation ou non.
 */
export const estInterdit = (f: FournisseurConnu, ultra: boolean, cloudUltra: boolean) =>
  ultra && estEnLigne(f) && (f.etat === 'gratuit' || !cloudUltra)

/** « En ligne » au sens de l'écran : tout ce qui n'est pas local, un « cloud privé » compris. */
export const estEnLigne = (f: FournisseurConnu) => f.lieu !== 'local'

/**
 * Le libellé d'un fournisseur dans une liste PLATE, sans section « Local » / « En ligne » autour : le nom, puis le lieu en un mot
 * (« MLX · Local », « Mistral · En ligne »). Dans le sélecteur, ce sont les sections qui disent où : le nom seul suffit (`LIBELLES`).
 */
export function libelleAvecLieu(id: string, lieu: LieuFournisseur, t: Traduire): string {
  const nom = LIBELLES[id] ? t(LIBELLES[id]) : id
  // Un « cloud privé » est rangé avec « En ligne » : le lieu se dit avec les deux mots de la règle, jamais avec un troisième.
  const cle: MessageKey = lieu === 'local' ? 'fournisseur.lieu.local' : 'fournisseur.enLigne'
  return `${nom} · ${t(cle)}`
}
