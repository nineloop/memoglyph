/**
 * La table unique des libellés de fournisseurs et la lecture de leur état.
 * Un test interdit aussi d'écrire un libellé de fournisseur dans un composant : il échapperait au test de règle d'écriture,
 * qui ne lit que les catalogues.
 */
import { describe, expect, it } from 'vitest'

import type { AppConfig } from '../api'
import { LIBELLES, estEnLigne, estPret, fournisseursConnus, libelleAvecLieu } from '../lib/fournisseurs'
import { CATALOGUES } from '../lib/messages'

// Le code source des composants et de l'application (son en-tête nomme les fournisseurs prêts), lu comme texte (même mécanisme que les autres tests de source).
const SOURCES = import.meta.glob(['../components/*.tsx', '../App.tsx'], { query: '?raw', import: 'default', eager: true }) as Record<string, string>

const NOMS = ['OpenRouter', 'Groq', 'OpenAI', 'Anthropic', 'Mistral', 'MLX']

/** Les noms de fournisseurs écrits DANS UNE CHAÎNE d'une ligne de source (commentaires exclus). */
function libellesEcrits(ligne: string): string[] {
  const sansCommentaire = ligne.replace(/\/\/.*$/, '').replace(/\/\*.*?\*\//g, '')
  return NOMS.filter((nom) => new RegExp(`['"\`][^'"\`]*\\b${nom}\\b[^'"\`]*['"\`]`).test(sansCommentaire))
}

const etats = {
  groq: { lieu: 'en_ligne', etat: 'pret' }, compatible: { lieu: 'cloud_prive', etat: 'non_configure' },
  mlx: { lieu: 'local', etat: 'pret' }, openrouter: { lieu: 'en_ligne', etat: 'gratuit' }, mistral: { lieu: 'en_ligne', etat: 'non_configure' },
} as const

describe('les libellés', () => {
  it('chaque identifiant du registre a une clé de message qui existe en français et en anglais', () => {
    expect(Object.keys(LIBELLES).sort()).toEqual(['anthropic', 'compatible', 'groq', 'mistral', 'mlx', 'openai', 'openrouter'])
    for (const cle of Object.values(LIBELLES)) {
      expect(CATALOGUES.fr[cle], cle).toBeTruthy()
      expect(CATALOGUES.en[cle], cle).toBeTruthy()
    }
  })

  it('la détection voit un libellé écrit dans une chaîne, pas un commentaire ni un identifiant', () => {
    expect(libellesEcrits("const nom = 'Anthropic (Claude)'")).toEqual(['Anthropic'])
    expect(libellesEcrits('const nom = `Chez Groq`')).toEqual(['Groq'])
    expect(libellesEcrits("// Mistral apporte aussi la transcription")).toEqual([])
    expect(libellesEcrits('/* OpenAI */ const x = 1')).toEqual([])
    expect(libellesEcrits("const p = 'mistral'")).toEqual([])                              // un identifiant, pas un libellé
  })

  it('aucun composant n\'écrit lui-même un libellé de fournisseur', () => {
    const composants = Object.entries(SOURCES)
    expect(composants.length).toBeGreaterThan(20)                                      // ancre : le balayage voit les composants
    const fautes: string[] = []
    for (const [chemin, source] of composants) {
      source.split('\n').forEach((ligne, i) => {
        for (const nom of libellesEcrits(ligne)) fautes.push(`${chemin}:${i + 1} ${nom}`)
      })
    }
    expect(fautes).toEqual([])
  })
})

describe('une seule table', () => {
  it("aucun composant ne porte sa propre table d'identifiants vers des clés `app.provider.*`", () => {
    const composants = Object.entries(SOURCES)
    expect(composants.length).toBeGreaterThan(20)
    const fautes = composants
      .filter(([, source]) => /['"]app\.provider\./.test(source))
      .map(([chemin]) => chemin)
    expect(fautes).toEqual([])
  })
})

describe('les fournisseurs connus', () => {
  it('rangent le modèle local d\'abord, puis les fournisseurs en ligne, puis le cloud privé, et donnent leur état', () => {
    const liste = fournisseursConnus({ llm_etats: etats } as unknown as AppConfig)
    expect(liste.map((f) => f.id)).toEqual(['mlx', 'groq', 'openrouter', 'mistral', 'compatible'])
    expect(liste.find((f) => f.id === 'openrouter')?.etat).toBe('gratuit')
  })

  it('« prêt » et « modèle gratuit » se choisissent, « non configuré » jamais ; « en ligne » inclut le cloud privé', () => {
    const liste = Object.fromEntries(fournisseursConnus({ llm_etats: etats } as unknown as AppConfig).map((f) => [f.id, f]))
    expect(estPret(liste.groq) && estPret(liste.openrouter) && estPret(liste.mlx)).toBe(true)
    expect(estPret(liste.mistral) || estPret(liste.compatible)).toBe(false)
    expect(estEnLigne(liste.compatible) && estEnLigne(liste.groq)).toBe(true)
    expect(estEnLigne(liste.mlx)).toBe(false)
  })

  it('sans `llm_etats` (ancien serveur), l\'état se déduit de `llm_ready` et des listes locales', () => {
    const liste = fournisseursConnus({
      llm_providers: ['mlx', 'mistral', 'openai', 'cloud'], local_providers: ['mlx'], private_cloud_providers: [],
      llm_ready: { mlx: true, mistral: false, openai: true, cloud: true },
    } as unknown as AppConfig)
    expect(liste).toEqual([
      { id: 'mlx', lieu: 'local', etat: 'pret' },
      { id: 'mistral', lieu: 'en_ligne', etat: 'non_configure' },
      { id: 'openai', lieu: 'en_ligne', etat: 'pret' },
    ])
  })
})

describe('le libellé avec son lieu (listes plates)', () => {
  const tr = (langue: 'fr' | 'en') => (cle: string) => CATALOGUES[langue][cle as keyof typeof CATALOGUES.fr] as string
  it('le nom, puis « Local » ou « En ligne » (un cloud privé est en ligne), en français et en anglais', () => {
    expect(libelleAvecLieu('mlx', 'local', tr('fr') as never)).toBe('MLX · Local')
    expect(libelleAvecLieu('mistral', 'en_ligne', tr('fr') as never)).toBe('Mistral · En ligne')
    expect(libelleAvecLieu('compatible', 'cloud_prive', tr('fr') as never)).toBe('Serveur compatible OpenAI · En ligne')
    expect(libelleAvecLieu('mistral', 'en_ligne', tr('en') as never)).toBe('Mistral · Online')
    expect(libelleAvecLieu('compatible', 'local', tr('en') as never)).toBe('OpenAI-compatible server · Local')
  })
  it('un identifiant sans libellé garde son identifiant', () => {
    expect(libelleAvecLieu('inconnu', 'en_ligne', tr('fr') as never)).toBe('inconnu · En ligne')
  })
})
