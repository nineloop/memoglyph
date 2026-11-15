/**
 * Les réglages de traitement retenus par le compte : les appels à l'API, et le retour sur « Local » quand ce qui a été
 * retenu n'existe plus (fournisseur retiré, clé retirée). Le serveur garde ce qui a été écrit ; c'est l'écran qui corrige.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getReglagesTraitement, setReglagesTraitement, type AppConfig, type ReglagesTraitement } from '../api'
import { fournisseurLocal, fournisseurRetenuIndisponible, reglagesApplicables, sensibiliteApplicable, sensibiliteRetenable } from '../lib/reglagesTraitement'

afterEach(() => vi.unstubAllGlobals())

const RETENUS: ReglagesTraitement = {
  fournisseur: 'groq', langue: 'fr', resultats: { compte_rendu: true, titre_resume: true, locuteurs: false },
}

const config = (extra: Partial<AppConfig> = {}): AppConfig => ({
  default_llm_provider: 'mlx', local_providers: ['mlx'],
  llm_etats: {
    mlx: { lieu: 'local', etat: 'pret' },
    groq: { lieu: 'en_ligne', etat: 'pret' },
    openrouter: { lieu: 'en_ligne', etat: 'gratuit' },
    mistral: { lieu: 'en_ligne', etat: 'non_configure' },
  },
  ...extra,
} as AppConfig)

describe('les appels', () => {
  it('lit les réglages retenus, ou null', async () => {
    const fetchSimule = vi.fn().mockResolvedValue(new Response(JSON.stringify({ reglages: null }), { status: 200 }))
    vi.stubGlobal('fetch', fetchSimule)
    expect(await getReglagesTraitement()).toEqual({ reglages: null })
    expect(fetchSimule).toHaveBeenCalledWith('/api/compte/reglages-traitement')
  })

  it('écrit les réglages en PUT, au format JSON, et rend ce que le serveur relit', async () => {
    const fetchSimule = vi.fn().mockResolvedValue(new Response(JSON.stringify({ reglages: RETENUS }), { status: 200 }))
    vi.stubGlobal('fetch', fetchSimule)
    expect(await setReglagesTraitement(RETENUS)).toEqual({ reglages: RETENUS })
    const [url, options] = fetchSimule.mock.calls[0]
    expect(url).toBe('/api/compte/reglages-traitement')
    expect(options.method).toBe('PUT')
    expect(JSON.parse(options.body)).toEqual(RETENUS)
  })

  it('refuse proprement un refus du serveur', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'non' }), { status: 422 })))
    await expect(setReglagesTraitement(RETENUS)).rejects.toThrow()
  })
})

describe('un fournisseur retenu mais pas prêt', () => {
  it('se nomme (pour le dire à la personne) quand il est connu mais non configuré', () => {
    expect(fournisseurRetenuIndisponible({ ...RETENUS, fournisseur: 'mistral' }, config())).toBe('mistral')
  })

  it('ne se nomme pas quand il est prêt, disparu du registre, ou quand rien n\'est retenu', () => {
    expect(fournisseurRetenuIndisponible(RETENUS, config())).toBeNull()                                    // groq est prêt
    expect(fournisseurRetenuIndisponible({ ...RETENUS, fournisseur: 'disparu' }, config())).toBeNull()      // inconnu : rien à nommer
    expect(fournisseurRetenuIndisponible(null, config())).toBeNull()
  })
})

describe('le retour sur « Local »', () => {
  it('le modèle local est le fournisseur par défaut quand il est local, sinon le premier local, sinon mlx', () => {
    expect(fournisseurLocal(config())).toBe('mlx')
    expect(fournisseurLocal(config({ default_llm_provider: 'groq', local_providers: ['serveur-local'] }))).toBe('serveur-local')
    expect(fournisseurLocal(config({ default_llm_provider: undefined, local_providers: [] }))).toBe('mlx')
  })

  it('garde un fournisseur prêt ou gratuit', () => {
    expect(reglagesApplicables(RETENUS, config())).toEqual(RETENUS)
    expect(reglagesApplicables({ ...RETENUS, fournisseur: 'openrouter' }, config())?.fournisseur).toBe('openrouter')
  })

  it.each([
    ['un fournisseur disparu', 'disparu'],
    ['un fournisseur dont la clé a été retirée', 'mistral'],
  ])('retombe sur « Local » pour %s, en gardant la langue et les résultats', (_cas, fournisseur) => {
    const lus = reglagesApplicables({ ...RETENUS, fournisseur }, config())
    expect(lus).toEqual({ ...RETENUS, fournisseur: 'mlx' })
  })

  it('ne dit rien quand rien n\'a été retenu', () => {
    expect(reglagesApplicables(null, config())).toBeNull()
  })

  it('sans `llm_etats` (ancien serveur), l\'état se déduit de `llm_ready` : prêt = gardé, sinon « Local »', () => {
    const ancien = { llm_etats: undefined, llm_providers: ['mlx', 'groq', 'mistral'], llm_ready: { mlx: true, groq: true, mistral: false } }
    expect(reglagesApplicables(RETENUS, config(ancien))).toEqual(RETENUS)
    expect(reglagesApplicables({ ...RETENUS, fournisseur: 'mistral' }, config(ancien))).toEqual({ ...RETENUS, fournisseur: 'mlx' })
    expect(reglagesApplicables(RETENUS, config({ llm_etats: undefined }))).toEqual({ ...RETENUS, fournisseur: 'mlx' })   // rien de connu : Local
  })
})

describe('la sensibilité retenue : jamais en dessous du défaut', () => {
  const NIVEAUX = ['ultra_confidential', 'confidential', 'standard']
  const avec = (defaut: string) => ({ sensitivity_levels: NIVEAUX, default_sensitivity: defaut })

  it('appliquée : la plus stricte des deux (le défaut de l\'instance, la sensibilité retenue)', () => {
    expect(sensibiliteApplicable('confidential', avec('standard'))).toBe('confidential')
    expect(sensibiliteApplicable('ultra_confidential', avec('confidential'))).toBe('ultra_confidential')
    expect(sensibiliteApplicable('confidential', avec('ultra_confidential'))).toBe('ultra_confidential')     // le défaut gagne
    expect(sensibiliteApplicable('standard', avec('confidential'))).toBe('confidential')                      // jamais en dessous
    expect(sensibiliteApplicable('confidential', avec('confidential'))).toBe('confidential')
  })

  it('rien de retenu : le défaut de l\'instance', () => {
    expect(sensibiliteApplicable(undefined, avec('standard'))).toBe('standard')
    expect(sensibiliteApplicable(undefined, avec('confidential'))).toBe('confidential')
  })

  it('un niveau inconnu ne bat rien ; un défaut absent vaut « standard »', () => {
    expect(sensibiliteApplicable('secret', avec('confidential'))).toBe('confidential')
    expect(sensibiliteApplicable('confidential', { sensitivity_levels: NIVEAUX })).toBe('confidential')
    expect(sensibiliteApplicable(undefined, {})).toBe('standard')
  })

  it('retenable : seulement ce qui est STRICTEMENT plus strict que le défaut (l\'égalité ne se retient pas)', () => {
    expect(sensibiliteRetenable('confidential', avec('standard'))).toBe('confidential')
    expect(sensibiliteRetenable('ultra_confidential', avec('standard'))).toBe('ultra_confidential')
    expect(sensibiliteRetenable('standard', avec('standard'))).toBeUndefined()
    expect(sensibiliteRetenable('confidential', avec('confidential'))).toBeUndefined()
    expect(sensibiliteRetenable('standard', avec('confidential'))).toBeUndefined()
    expect(sensibiliteRetenable('confidential', avec('ultra_confidential'))).toBeUndefined()
    expect(sensibiliteRetenable('ultra_confidential', avec('confidential'))).toBe('ultra_confidential')
  })
})
