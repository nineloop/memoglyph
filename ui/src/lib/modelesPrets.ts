import type { AppConfig } from '../api'

/**
 * Les modèles proposés MAINTENANT : les fournisseurs que l'interface propose ET que la configuration dit prêts
 * (`llm_ready`), le modèle de cette machine d'abord. Un fournisseur non configuré n'y figure pas.
 */
export function modelesPrets(config: Pick<AppConfig, 'llm_providers' | 'llm_ready' | 'local_providers'>): string[] {
  const proposes = (config.llm_providers ?? []).filter((p) => p !== 'cloud')
  const prets = proposes.filter((p) => config.llm_ready?.[p] === true)
  const locaux = config.local_providers ?? ['mlx']
  return [...prets.filter((p) => locaux.includes(p)), ...prets.filter((p) => !locaux.includes(p))]
}
