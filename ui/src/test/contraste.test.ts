import { describe, expect, it } from 'vitest'
import indexCss from '../index.css?raw'
import themeCss from '../design/theme.css?raw'
import { composer, couleurDuJeton, jetons, lireCouleur, rapport } from '../lib/contraste'

const noir = lireCouleur('#000000')!
const blanc = lireCouleur('#ffffff')!

/** Les jetons du thème clair, et ceux du sombre (surcharges posées sur le clair). */
const CLAIR = jetons(indexCss, ':root')
const SOMBRE = { ...CLAIR, ...jetons(themeCss, ":root[data-theme='dark']") }

function fondDePage(table: Record<string, string>) {
  return couleurDuJeton(table, '--bg')!
}

describe('le calcul de contraste', () => {
  it('connaît les valeurs de référence', () => {
    expect(rapport(blanc, noir)).toBeCloseTo(21, 1)
    expect(rapport(blanc, blanc)).toBeCloseTo(1, 5)
    // #767676 sur blanc : la limite classique de 4,5:1.
    expect(rapport(lireCouleur('#767676')!, blanc)).toBeGreaterThan(4.5)
    expect(rapport(lireCouleur('#777777')!, blanc)).toBeLessThan(4.6)
  })

  it('compose la transparence sur le fond avant de comparer', () => {
    const verre = lireCouleur('rgb(255 255 255 / 92%)')!
    expect(verre.a).toBeCloseTo(0.92)
    const compose = composer(verre, fondDePage(CLAIR))
    expect(compose.r).toBeGreaterThan(fondDePage(CLAIR).r - 1)
  })

  it('lit et résout les jetons des deux thèmes', () => {
    expect(couleurDuJeton(CLAIR, '--bg')).not.toBeNull()
    expect(couleurDuJeton(CLAIR, '--text')).not.toBeNull()
    expect(couleurDuJeton(SOMBRE, '--bg')).not.toBeNull()
    expect(rapport(fondDePage(CLAIR), fondDePage(SOMBRE))).toBeGreaterThan(5)
  })
})

describe.each([['clair', CLAIR], ['sombre', SOMBRE]] as const)('contrastes du thème %s', (_nom, table) => {
  it('le texte courant sur la page et sur les panneaux est lisible (≥ 4,5:1)', () => {
    for (const fond of ['--bg', '--panel']) {
      expect(rapport(couleurDuJeton(table, '--text')!, couleurDuJeton(table, fond)!)).toBeGreaterThanOrEqual(4.5)
      expect(rapport(couleurDuJeton(table, '--text-h')!, couleurDuJeton(table, fond)!)).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('le texte secondaire (légendes, état) reste lisible (≥ 4,5:1)', () => {
    expect(rapport(couleurDuJeton(table, '--muted')!, couleurDuJeton(table, '--panel')!)).toBeGreaterThanOrEqual(4.5)
  })

  it('les bordures de composants se distinguent du fond (≥ 3:1)', () => {
    expect(rapport(couleurDuJeton(table, '--border-strong')!, couleurDuJeton(table, '--panel')!)).toBeGreaterThanOrEqual(3)
  })
})

describe('le dock se détache du fond (retour 4, SC-010)', () => {
  it('thème clair : le dock est au moins 3:1 plus foncé que la page', () => {
    const dock = couleurDuJeton(CLAIR, '--dock-bg')
    expect(dock, 'le jeton --dock-bg manque dans index.css').not.toBeNull()
    expect(rapport(composer(dock!, fondDePage(CLAIR)), fondDePage(CLAIR))).toBeGreaterThanOrEqual(3)
  })

  it('le texte du dock reste lisible sur son fond, dans les deux thèmes (≥ 4,5:1)', () => {
    for (const table of [CLAIR, SOMBRE]) {
      const fond = couleurDuJeton(table, '--dock-bg')
      const texte = couleurDuJeton(table, '--dock-fg')
      expect(fond, 'jeton --dock-bg').not.toBeNull()
      expect(texte, 'jeton --dock-fg').not.toBeNull()
      expect(rapport(texte!, composer(fond!, fondDePage(table)))).toBeGreaterThanOrEqual(4.5)
    }
  })

  it("l'état actuel (verre blanc à 92 %) ne passe pas : le test peut échouer", () => {
    const verre = couleurDuJeton(CLAIR, '--panel-glass')!
    expect(rapport(composer(verre, fondDePage(CLAIR)), fondDePage(CLAIR))).toBeLessThan(3)
  })
})

describe.each([['clair', CLAIR], ['sombre', SOMBRE]] as const)('pastilles d\'état des traitements, thème %s', (_nom, table) => {
  // Le fond de chaque pastille est `color-mix(<jeton> 16 %, transparent)` posé sur la carte (`--panel-2`) :
  // on le recompose, puis on mesure le texte (≥ 4,5:1). « Terminé » est du vert sur du vert pâle : le cas risqué.
  const sur = (jeton: string) => {
    const fond = couleurDuJeton(table, '--panel-2')!
    const teinte = couleurDuJeton(table, jeton)!
    return composer({ ...teinte, a: 0.16 }, fond)
  }
  it.each([
    ['En cours', '--accent', '--accent-bg'],
    // Le texte est `--text-h` (le vert `--ok` sur son propre fond pâle n'atteignait que 4,2:1) ; seule l'icône est verte.
    ['Terminé', '--text-h', '--ok'],
    ['Échec', '--text-h', '--off'],
    ['En attente', '--text-h', '--warn'],
  ] as const)('%s reste lisible (≥ 4,5:1)', (_etat, texte, teinte) => {
    const fond = teinte === '--accent-bg' ? couleurDuJeton(table, '--accent-bg')! : sur(teinte)
    expect(rapport(couleurDuJeton(table, texte)!, fond)).toBeGreaterThanOrEqual(4.5)
  })
})

describe.each([['clair', CLAIR], ['sombre', SOMBRE]] as const)('boutons d\'action du modèle (« Régénérer ce compte rendu »), thème %s', (_nom, table) => {
  // `.btn.accent.llm` : le texte est `--llm`, le fond `--llm-bg`. Une capture du thème sombre le montrait terne :
  // la paire est mesurée plutôt que jugée à l'œil (texte courant ≥ 4,5:1).
  it('le texte violet reste lisible sur son fond (≥ 4,5:1)', () => {
    expect(rapport(couleurDuJeton(table, '--llm')!, couleurDuJeton(table, '--llm-bg')!)).toBeGreaterThanOrEqual(4.5)
  })
  it('au survol, le texte sur le fond violet plein reste lisible (≥ 4,5:1)', () => {
    expect(rapport(couleurDuJeton(table, '--accent-text')!, couleurDuJeton(table, '--llm')!)).toBeGreaterThanOrEqual(4.5)
  })
})
