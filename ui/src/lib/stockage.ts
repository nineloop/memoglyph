/**
 * Clés du navigateur, toutes sous le préfixe `memoglyph.`.
 *
 * Les appelants gardent leur `try/catch` : `localStorage` peut lever (navigation
 * privée, quota), et chacun sait quoi faire sans lui.
 */

const PREFIXE = 'memoglyph.'

export const cle = (suffixe: string): string => PREFIXE + suffixe

export function lireCle(suffixe: string): string | null {
  return localStorage.getItem(cle(suffixe))
}

export function ecrireCle(suffixe: string, valeur: string): void {
  localStorage.setItem(cle(suffixe), valeur)
}

export function retirerCle(suffixe: string): void {
  localStorage.removeItem(cle(suffixe))
}
