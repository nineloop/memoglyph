import { createContext, useContext, useMemo } from 'react'
import { currentLang, setLangOverride, translate } from './i18n'
import type { Lang, Traduire } from './i18n'

export type LangValue = {
  lang: Lang
  setLang: (lang: Lang) => void
  t: Traduire
}

export const LangContext = createContext<LangValue | null>(null)

/**
 * Utilisable SANS `LangProvider` au-dessus : un composant monté isolément —
 * un test, un futur point d'entrée — retombe sur la langue détectée au lieu de
 * planter. Le `setLang` de secours persiste le choix sans re-rendu : il n'y a
 * alors personne à prévenir.
 *
 * Les deux hooks sont appelés inconditionnellement : la valeur de secours est
 * calculée même quand un provider existe, pour ne pas violer l'ordre des hooks.
 */
export function useLang(): LangValue {
  const fourni = useContext(LangContext)
  const secours = useMemo<LangValue>(() => {
    const lang = currentLang()
    return { lang, setLang: setLangOverride, t: (key, params) => translate(lang, key, params) }
  }, [])
  return fourni ?? secours
}
