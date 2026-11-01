import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'

import { LangProvider } from '../components/LangProvider'
import { ScopeControl } from '../components/ScopeControl'
import { ReportView } from '../components/ReportView'
import type { Lang } from '../lib/i18n'

/**
 * Preuve de CÂBLAGE, pas de catalogue : `i18n.test.ts` garantit déjà la parité
 * des clés ; ici on vérifie qu'une surface réellement migrée rend bien la LANGUE
 * demandée. Le montage se fait sous `LangProvider initiale=…`, le même chemin
 * que celui qu'emprunte l'application — pas un montage dégradé.
 */
function rendre(lang: Lang, node: React.ReactNode) {
  return render(<LangProvider initiale={lang}>{node}</LangProvider>)
}

it('ScopeControl suit la langue de l’interface', () => {
  const { unmount } = rendre('fr',
    <ScopeControl allProjects canToggle={false} scopeLabel="Climat" onToggle={() => {}} detail="" />)
  expect(screen.getByText('Portée : Tous mes projets')).toBeInTheDocument()
  unmount()

  rendre('en',
    <ScopeControl allProjects={false} canToggle scopeLabel="Climate" onToggle={() => {}} detail="" />)
  expect(screen.getByText('Scope: Project Climate')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Search across all my projects' })).toBeInTheDocument()
})

it('ReportView traduit le genre et le repli des sources', () => {
  const contenu = 'Corps du compte-rendu.'
  const { unmount } = rendre('fr', <ReportView content={contenu} genre="analyse_sportive" />)
  expect(screen.getByText('Analyse sportive')).toBeInTheDocument()
  unmount()

  rendre('en', <ReportView content={contenu} genre="analyse_sportive" />)
  expect(screen.getByText('Sports analysis')).toBeInTheDocument()
})
