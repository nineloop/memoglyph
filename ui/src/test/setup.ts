import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'

afterEach(cleanup)

// jsdom expose bien un objet `localStorage`, mais SANS `setItem` ni `getItem`
// (sondé : `typeof localStorage.setItem === 'undefined'`). Tout le code
// de persistance — `llmProvider.ts` comme la langue — entoure ses accès d'un
// `try/catch` : sous les tests, il tombait donc silencieusement dans le `catch`.
// Autrement dit, aucune persistance n'était réellement exercée, et un test qui
// aurait prétendu la vérifier serait passé sur du vide.
if (typeof localStorage?.setItem !== 'function') {
  const memoire = new Map<string, string>()
  const faux: Storage = {
    get length() { return memoire.size },
    key: (index) => [...memoire.keys()][index] ?? null,
    getItem: (cle) => (memoire.has(cle) ? memoire.get(cle)! : null),
    setItem: (cle, valeur) => { memoire.set(String(cle), String(valeur)) },
    removeItem: (cle) => { memoire.delete(cle) },
    clear: () => { memoire.clear() },
  }
  Object.defineProperty(globalThis, 'localStorage', { value: faux, configurable: true })
  if (typeof window !== 'undefined') {
    Object.defineProperty(window, 'localStorage', { value: faux, configurable: true })
  }
}

// L'interface est bilingue et se règle par défaut sur le navigateur. jsdom
// annonce `en-US` : sans surcharge explicite, tous les tests écrits contre les
// libellés français basculeraient en anglais selon l'environnement. On fixe la
// langue de référence ICI, et les tests qui vérifient l'autre langue montent un
// `LangProvider` explicite. On repart aussi d'un stockage vide, pour qu'un test
// n'hérite pas du fournisseur LLM choisi par le précédent.
beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('memoglyph.lang', 'fr')
})
