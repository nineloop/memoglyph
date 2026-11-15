import { describe, expect, it } from 'vitest'

/**
 * Aucune boîte native dans l'interface : ni `confirm(`, ni `alert(`, ni `prompt(`.
 *
 * Ces boîtes sortent de la charte (non stylables, sans focus géré, bloquantes) et, pour `alert`, perdent l'erreur à la
 * fermeture. La confirmation passe par `useConfirmer` (`design/Confirmation.tsx`), l'échec par une phrase sur l'écran.
 * Le garde lit le code de `ui/src` hors tests ; les commentaires ne comptent pas.
 */

const SOURCES = import.meta.glob(['../**/*.ts', '../**/*.tsx', '!../test/**'], {
  query: '?raw', import: 'default', eager: true,
}) as Record<string, string>

/** Le code sans ses commentaires (remplacés par des blancs). */
export function sansCommentaires(code: string): string {
  let sortie = ''
  let i = 0
  while (i < code.length) {
    if (code[i] === '/' && code[i + 1] === '/') {
      while (i < code.length && code[i] !== '\n') { sortie += ' '; i++ }
    } else if (code[i] === '/' && code[i + 1] === '*') {
      while (i < code.length && !(code[i] === '*' && code[i + 1] === '/')) { sortie += code[i] === '\n' ? '\n' : ' '; i++ }
      sortie += '  '; i += 2
    } else { sortie += code[i]; i++ }
  }
  return sortie
}

const NATIVE = /(?<![\w$.])(?:window\.|globalThis\.|self\.)?(confirm|alert|prompt)\s*\(/g

export function appelsNatifs(code: string): string[] {
  return [...sansCommentaires(code).matchAll(NATIVE)].map((m) => m[1])
}

describe('le détecteur de boîtes natives', () => {
  it('voit un appel nu ou par `window`, et un `prompt`', () => {
    expect(appelsNatifs('if (!confirm(msg)) return')).toEqual(['confirm'])
    expect(appelsNatifs('const ok = window.confirm(t("x"))')).toEqual(['confirm'])
    expect(appelsNatifs('window.alert(String(e))')).toEqual(['alert'])
    expect(appelsNatifs('const nom = prompt("Nom ?")')).toEqual(['prompt'])
  })

  it('ne voit ni un commentaire, ni une autre fonction qui commence pareil', () => {
    expect(appelsNatifs('// on appelait confirm(msg) ici')).toEqual([])
    expect(appelsNatifs('/* alert(1) */ const x = 1')).toEqual([])
    expect(appelsNatifs('await confirmer({ titre })')).toEqual([])
    expect(appelsNatifs('if (!await confirmer(demande)) return')).toEqual([])
    expect(appelsNatifs('espion.confirm(x)')).toEqual([])
    expect(appelsNatifs('await confirmerLaFenetre(user)')).toEqual([])
    expect(appelsNatifs('t(\'traitements.cancel.confirm\')')).toEqual([])
  })
})

describe('l\'interface sans boîte native', () => {
  it('a bien lu le code de l\'interface', () => {
    const noms = Object.keys(SOURCES)
    expect(noms.length).toBeGreaterThan(50)
    expect(noms.some((n) => n.endsWith('/components/TrashPanel.tsx'))).toBe(true)
    expect(noms.some((n) => n.endsWith('/design/Confirmation.tsx'))).toBe(true)
    expect(noms.some((n) => n.includes('/test/'))).toBe(false)
  })

  it('n\'appelle jamais confirm(), alert() ni prompt()', () => {
    const trouves = Object.entries(SOURCES)
      .map(([fichier, code]) => [fichier, appelsNatifs(code)] as const)
      .filter(([, appels]) => appels.length > 0)
      .map(([fichier, appels]) => `${fichier.replace('../', 'ui/src/')} : ${appels.join(', ')}`)
    expect(trouves).toEqual([])
  })
})
