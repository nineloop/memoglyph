import { getConfig, getReglagesTraitement, type AppConfig, type ReglagesTraitement } from '../api'
import { FOURNISSEUR_LOCAL_DE_REPLI, estPret, fournisseursConnus } from './fournisseurs'

/**
 * Le fournisseur « Local » : celui du serveur par défaut s'il est local, sinon le premier fournisseur local, sinon le modèle
 * de cette installation (`mlx`). Jamais un nom écrit à l'écran : l'identifiant seul.
 */
export function fournisseurLocal(config: Pick<AppConfig, 'default_llm_provider' | 'local_providers'>): string {
  const locaux = config.local_providers ?? []
  if (config.default_llm_provider && locaux.includes(config.default_llm_provider)) return config.default_llm_provider
  return locaux[0] ?? 'mlx'
}

/**
 * Les réglages retenus, corrigés pour ce que le serveur propose MAINTENANT : un fournisseur qui n'existe plus ou qui n'est
 * plus prêt (clé retirée) retombe sur « Local », sans message ; la langue et les résultats restent. Le serveur, lui, garde ce
 * qui a été écrit : c'est l'écran qui sait ce qu'il propose.
 */
export function reglagesApplicables(
  reglages: ReglagesTraitement | null,
  config: Pick<AppConfig, 'default_llm_provider' | 'local_providers' | 'llm_etats' | 'llm_providers' | 'llm_ready' | 'private_cloud_providers'>,
): ReglagesTraitement | null {
  if (!reglages) return null
  const retenu = fournisseursConnus(config).find((f) => f.id === reglages.fournisseur)
  const valable = retenu !== undefined && estPret(retenu)
  return valable ? reglages : { ...reglages, fournisseur: fournisseurLocal(config) }
}

const NIVEAUX_PAR_DEFAUT = ['ultra_confidential', 'confidential', 'standard']
type ConfigSensibilite = Pick<AppConfig, 'sensitivity_levels' | 'default_sensitivity'>

/** Le rang d'un niveau : 0 = le plus sensible. Un niveau inconnu vaut le plus ouvert (il ne bat rien). */
function rang(niveau: string | undefined, config: ConfigSensibilite): number {
  const niveaux = config.sensitivity_levels?.length ? config.sensitivity_levels : NIVEAUX_PAR_DEFAUT
  const i = niveaux.indexOf(niveau ?? '')
  return i === -1 ? niveaux.length - 1 : i
}

const defautDe = (config: ConfigSensibilite) => config.default_sensitivity || 'standard'

/** `niveau` est-il STRICTEMENT plus sensible que le défaut de l'instance ? L'égalité vaut non. */
const plusStricteQueLeDefaut = (niveau: string | undefined, config: ConfigSensibilite): boolean =>
  rang(niveau, config) < rang(defautDe(config), config)

/**
 * La sensibilité qui s'applique à l'ouverture : la plus stricte des deux, le défaut de l'instance et la sensibilité retenue par le
 * compte. Si le défaut monte au-dessus du choix retenu, c'est le défaut qui s'applique ; le compte n'est pas réécrit.
 */
export function sensibiliteApplicable(retenue: string | undefined, config: ConfigSensibilite): string {
  return plusStricteQueLeDefaut(retenue, config) ? (retenue as string) : defautDe(config)
}

/** Ce que « Retenir » écrit pour la sensibilité : le niveau courant s'il est plus strict que le défaut, sinon rien (`undefined`). */
export function sensibiliteRetenable(courante: string, config: ConfigSensibilite): string | undefined {
  return plusStricteQueLeDefaut(courante, config) ? courante : undefined
}

/**
 * Le fournisseur retenu par le compte quand il est CONNU du serveur mais pas prêt (clé retirée) : l'écran le dit, avec son nom, en
 * une ligne (« … le traitement se fera en Local »). Disparu du registre, prêt, ou rien de retenu : rien à dire (`null`).
 */
export function fournisseurRetenuIndisponible(
  reglages: ReglagesTraitement | null,
  config: Pick<AppConfig, 'llm_etats' | 'llm_providers' | 'llm_ready' | 'local_providers' | 'private_cloud_providers'>,
): string | null {
  if (!reglages) return null
  const retenu = fournisseursConnus(config).find((f) => f.id === reglages.fournisseur)
  return retenu && !estPret(retenu) ? retenu.id : null
}

/**
 * Le fournisseur de l'Assistant : celui que le compte a retenu s'il est prêt, sinon le local. Une configuration ou des réglages
 * illisibles donnent le local : la question n'est jamais bloquée par cette lecture. Rien ne vient du navigateur.
 */
export async function fournisseurDeLAssistant(): Promise<string> {
  try {
    const [config, retenus] = await Promise.all([getConfig(), getReglagesTraitement()])
    return reglagesApplicables(retenus.reglages, config)?.fournisseur ?? fournisseurLocal(config)
  } catch {
    return FOURNISSEUR_LOCAL_DE_REPLI
  }
}
