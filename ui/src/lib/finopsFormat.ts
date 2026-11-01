import type { Traduire } from './i18n'

// Formatage FinOps partagé (section document + tableau de bord Usage). Règles dures : devise
// NATIVE jamais convertie ni mélangée ; coût NULL => « non tarifé » (jamais 0) ; local => « ~ … estimé ».

/** Symbole de devise NATIF — jamais de conversion, jamais de mélange. */
export function currencySymbol(cur?: string | null): string {
  if (cur === 'USD') return '$'
  if (cur === 'EUR') return '€'
  return cur ? `${cur} ` : ''
}

/** Coût affiché avec son ÉTIQUETTE de base : NULL => « — unpriced » (jamais 0). */
export function formatCost(
  row: { cost?: number | null; currency?: string | null; cost_basis?: string | null },
  t: Traduire,
): string {
  if (row.cost === null || row.cost === undefined) return t('finops.unpriced')
  const sym = currencySymbol(row.currency)
  const n = row.cost
  const amount = n < 0.01 ? n.toFixed(5) : n.toFixed(4)
  const estimated = row.cost_basis === 'local_imputed'
  const basisKey =
    row.cost_basis === 'cloud_derived' ? 'finops.basis.derived'
    : row.cost_basis === 'local_imputed' ? 'finops.basis.estimated'
    : row.cost_basis === 'mixed' ? 'finops.basis.mixed'
    : null
  const money = `${estimated ? '~' : ''}${sym}${amount}`
  return basisKey ? `${money} ${t(basisKey)}` : money
}

export function formatTokens(n?: number | null): string {
  if (n === null || n === undefined) return '—'
  if (n >= 1000) {
    const k = n / 1000
    return `${k >= 10 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, '')}k`
  }
  return String(n)
}

export function formatTime(s?: number | null): string {
  if (s === null || s === undefined) return '—'
  const whole = Math.round(s)
  if (whole >= 60) return `${Math.floor(whole / 60)}m ${whole % 60}s`
  return `${whole} s`
}

/** Durée audio : même forme que le temps de traitement. */
export const formatAudio = formatTime
