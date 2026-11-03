/**
 * Rapports de contraste calculés sur les JETONS du thème (WCAG 2.x).
 *
 * jsdom ne calcule ni couleur ni mise en page : un test de contraste ne peut pas lire le
 * rendu. On lit donc les jetons déclarés dans `index.css` (thème clair) et `design/theme.css`
 * (sombre), on résout les `var(--…)`, on compose la transparence sur le fond, puis on applique
 * la formule du rapport de luminance. Utilisé par les tests, jamais par l'application.
 */

export type Couleur = { r: number; g: number; b: number; a: number }

const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i
const RGB = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/i

export function lireCouleur(texte: string): Couleur | null {
  const t = texte.trim()
  const hex = HEX.exec(t)
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].split('').map((c) => c + c).join('') : hex[1]
    return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16), a: 1 }
  }
  const rgb = RGB.exec(t)
  if (rgb) {
    const alpha = rgb[4] === undefined ? 1 : rgb[4].endsWith('%') ? parseFloat(rgb[4]) / 100 : parseFloat(rgb[4])
    return { r: +rgb[1], g: +rgb[2], b: +rgb[3], a: alpha }
  }
  return null
}

/** `avant` (éventuellement translucide) posé sur `arriere` (opaque). */
export function composer(avant: Couleur, arriere: Couleur): Couleur {
  const a = avant.a
  return {
    r: Math.round(avant.r * a + arriere.r * (1 - a)),
    g: Math.round(avant.g * a + arriere.g * (1 - a)),
    b: Math.round(avant.b * a + arriere.b * (1 - a)),
    a: 1,
  }
}

function lineaire(canal: number): number {
  const c = canal / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

export function luminance(c: Couleur): number {
  return 0.2126 * lineaire(c.r) + 0.7152 * lineaire(c.g) + 0.0722 * lineaire(c.b)
}

/** Rapport de contraste entre deux couleurs OPAQUES (compose d'abord le reste). */
export function rapport(a: Couleur, b: Couleur): number {
  const la = luminance(a)
  const lb = luminance(b)
  const [haut, bas] = la >= lb ? [la, lb] : [lb, la]
  return (haut + 0.05) / (bas + 0.05)
}

/** Les déclarations `--nom: valeur;` du premier bloc dont l'entête est exactement `selecteur`. */
export function jetons(css: string, selecteur: string): Record<string, string> {
  const debut = css.indexOf(`${selecteur} {`)
  if (debut < 0) return {}
  let profondeur = 0
  let fin = -1
  for (let i = css.indexOf('{', debut); i < css.length; i++) {
    if (css[i] === '{') profondeur++
    if (css[i] === '}' && --profondeur === 0) { fin = i; break }
  }
  const corps = css.slice(css.indexOf('{', debut) + 1, fin)
  const sansCommentaires = corps.replace(/\/\*[\s\S]*?\*\//g, '')
  const sortie: Record<string, string> = {}
  for (const decl of sansCommentaires.split(';')) {
    const m = /^\s*(--[\w-]+)\s*:\s*([\s\S]+?)\s*$/.exec(decl)
    if (m) sortie[m[1]] = m[2]
  }
  return sortie
}

/** Résout `var(--x)` de proche en proche ; `null` si la valeur n'est pas une couleur simple. */
export function couleurDuJeton(table: Record<string, string>, nom: string, profondeur = 0): Couleur | null {
  if (profondeur > 8) return null
  const valeur = table[nom]
  if (valeur === undefined) return null
  const reference = /^var\(\s*(--[\w-]+)\s*\)$/.exec(valeur.trim())
  if (reference) return couleurDuJeton(table, reference[1], profondeur + 1)
  return lireCouleur(valeur)
}
