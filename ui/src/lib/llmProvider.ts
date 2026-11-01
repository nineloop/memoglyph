/**
 * Choix de moteur LLM (Ollama local vs Mistral Cloud), partagé et PERSISTANT.
 *
 * Le choix vivait dans l'état local de TranscribePanel : les autres appels LLM
 * — compte-rendu à la demande, Assistant RAG — ne le voyaient pas et forçaient
 * Ollama. Un utilisateur Mistral-only ne pouvait donc générer aucun CR.
 *
 * On ne stocke QUE le provider. La clé API Mistral reste côté serveur (`.env`,
 * `config.MISTRAL_API_KEY`) : rien de sensible ne transite ni ne s'écrit ici.
 */

import { ecrireCle, lireCle } from './stockage'
const STORAGE_KEY = 'llmProvider'

// Le repli est MLX (Ollama sort de la cible). Il ne
// sert qu'aux appelants sans config sous la main — `api.ts` pour l'Assistant et
// le CR à la demande. `TranscribePanel` passe `default_llm_provider`, qui reste
// la source unique.
//
// Un choix déjà PERSISTÉ n'est pas écrasé ici. Un fournisseur sorti du
// registre (Ollama) est corrigé par `TranscribePanel`, qui retombe sur
// le défaut annoncé par l'API et réécrit le stockage.
export function getLlmProvider(fallback = 'mlx'): string {
  try {
    return lireCle(STORAGE_KEY) || fallback
  } catch {
    // localStorage indisponible (mode privé, quota) : on retombe sur le défaut.
    return fallback
  }
}

export function setLlmProvider(provider: string): void {
  try {
    ecrireCle(STORAGE_KEY, provider)
  } catch {
    /* stockage indisponible : le choix ne persiste pas, sans casser le flux. */
  }
}
