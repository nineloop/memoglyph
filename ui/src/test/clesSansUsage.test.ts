/**
 * Une clé de message de l'écran Transcrire (et de ses voisines) qui n'est lue nulle part est une clé morte : elle garde un texte que
 * plus rien n'affiche, et un texte mort se périme sans que personne le voie. Le test lit le code de l'application (pas les tests,
 * pas le catalogue) et refuse toute clé de ces familles qu'aucun fichier ne cite. Les commentaires ne comptent pas : un nom écrit
 * dans un commentaire n'est pas un usage.
 */
import { describe, expect, it } from 'vitest'

import { CATALOGUES } from '../lib/messages'

const SOURCES = import.meta.glob(
  ['../**/*.ts', '../**/*.tsx', '!../test/**', '!../lib/messages.ts'],
  { query: '?raw', import: 'default', eager: true },
) as Record<string, string>

const FAMILLES = [
  /^transcribe\./, /^app\.provider\./, /^spoken\.codeLabel$/,
  /^reader\.generate/, /^reader\.backTranscript$/, /^reader\.savedToLibrary$/, /^meeting\.report\.empty\./,
]

function sansCommentaires(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1')
}

describe('les clés de message sans usage', () => {
  const code = Object.values(SOURCES).map(sansCommentaires).join('\n')

  it('le balayage voit bien le code de l\'application', () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(40)
    expect(code).toContain("t('transcribe.submit')")
  })

  it('les commentaires ne comptent pas comme un usage', () => {
    expect(sansCommentaires("// t('x.y')\nconst a = 1 /* t('z.w') */")).not.toMatch(/x\.y|z\.w/)
    expect(sansCommentaires("const u = `${esquema}://hote` // t('q.r')")).toContain('${esquema}://hote')   // un « :// » n'ouvre pas un commentaire
  })

  it('chaque clé de ces familles est lue quelque part, dans les deux catalogues', () => {
    const mortes: string[] = []
    for (const cle of Object.keys(CATALOGUES.fr)) {
      if (!FAMILLES.some((f) => f.test(cle))) continue
      const citee = new RegExp(`['"\`]${cle.replace(/\./g, '\\.')}['"\`]`).test(code)
      if (!citee) mortes.push(cle)
    }
    expect(mortes).toEqual([])
    expect(Object.keys(CATALOGUES.en).sort()).toEqual(Object.keys(CATALOGUES.fr).sort())
  })
})
