import { useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { LangContext, useLang } from '../lib/langContext'
import type { LangValue } from '../lib/langContext'
import { LANGS, currentLang, setLangOverride, translate } from '../lib/i18n'
import type { Lang } from '../lib/i18n'

/**
 * Porte la langue courante pour tout l'arbre.
 *
 * `initiale` sert à deux choses : rendre le même composant dans les deux
 * langues depuis un test, et permettre plus tard une langue imposée par l'hôte.
 * Sans elle, on part de la surcharge persistée, sinon du navigateur.
 */
export function LangProvider({ children, initiale }: { children: ReactNode; initiale?: Lang }) {
  const [lang, setLangState] = useState<Lang>(() => initiale ?? currentLang())
  const setLang = useCallback((suivante: Lang) => {
    setLangState(suivante)
    setLangOverride(suivante)
  }, [])
  const valeur = useMemo<LangValue>(
    () => ({ lang, setLang, t: (key, params) => translate(lang, key, params) }),
    [lang, setLang],
  )
  return <LangContext.Provider value={valeur}>{children}</LangContext.Provider>
}

/** Sélecteur discret : deux options, aucune icône, dans la barre de contexte. */
export function LangSelector() {
  const { lang, setLang, t } = useLang()
  return (
    <select
      className="lang-select"
      aria-label={t('app.lang.label')}
      title={t('app.lang.label')}
      value={lang}
      onChange={(event) => setLang(event.target.value as Lang)}
    >
      {LANGS.map((candidate) => (
        <option key={candidate} value={candidate}>
          {t(candidate === 'fr' ? 'app.lang.fr' : 'app.lang.en')}
        </option>
      ))}
    </select>
  )
}
