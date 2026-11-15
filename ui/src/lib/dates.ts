import type { Lang } from './i18n'

const LOCALE: Record<Lang, string> = { fr: 'fr-FR', en: 'en-GB' }

/**
 * Le jour `AAAA-MM-JJ` (éventuellement suivi d'une heure) s'il existe au calendrier, sinon `null`.
 * Construit par ses éléments, à minuit UTC : aucun fuseau ne le décale (`new Date('AAAA-MM-JJ')` lu à l'ouest de
 * UTC donnerait la veille).
 */
function jourDe(valeur: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(valeur)
  if (!m) return null
  const [annee, mois, jour] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const date = new Date(Date.UTC(annee, mois - 1, jour))
  const existe = date.getUTCFullYear() === annee && date.getUTCMonth() === mois - 1 && date.getUTCDate() === jour
  return existe ? date : null
}

/** Français : `13/10/2026` ; anglais : `13 Oct 2026`. */
function optionsDuJour(lang: Lang): Intl.DateTimeFormatOptions {
  return lang === 'fr'
    ? { day: '2-digit', month: '2-digit', year: 'numeric' }
    : { day: 'numeric', month: 'short', year: 'numeric' }
}

/** Un jour `AAAA-MM-JJ`, dans la langue choisie ; `null` quand la valeur n'est pas un jour lisible. */
export function formaterJour(valeur: string, lang: Lang): string | null {
  const jour = jourDe(valeur)
  return jour ? new Intl.DateTimeFormat(LOCALE[lang], { ...optionsDuJour(lang), timeZone: 'UTC' }).format(jour) : null
}

/**
 * Un jour et une heure `AAAA-MM-JJTHH:MM…` lus tels quels (sans fuseau), dans la langue choisie ; sinon `null`.
 * Le séparateur entre le jour et l'heure vient d'`Intl`, comme pour l'horodatage des versions (`formaterInstant`).
 */
export function formaterJourHeure(valeur: string, lang: Lang): string | null {
  const heure = /^\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})/.exec(valeur)
  const jour = heure ? jourDe(valeur) : null
  if (!heure || !jour) return null
  const [heures, minutes] = [Number(heure[1]), Number(heure[2])]
  if (heures > 23 || minutes > 59) return null
  const instant = new Date(jour.getTime() + (heures * 60 + minutes) * 60_000)
  return new Intl.DateTimeFormat(LOCALE[lang], {
    ...optionsDuJour(lang), hour: '2-digit', minute: '2-digit', timeZone: 'UTC',
  }).format(instant)
}

/** Un instant (secondes depuis 1970), à l'heure locale et avec les secondes, dans la langue choisie. */
export function formaterInstant(secondes: number, lang: Lang): string {
  return new Intl.DateTimeFormat(LOCALE[lang], {
    ...optionsDuJour(lang), hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(secondes * 1000))
}
