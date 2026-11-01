import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { SearchMode, SearchResult } from '../api'
import { porteeDocument, search } from '../api'
import { TranscriptReader } from './TranscriptReader'
import { Icon } from './Icon'
import { displayDocumentTitle } from '../lib/meetings'
import { Badge } from '../design/primitives'
import { ScopeControl } from './ScopeControl'
import { useLatestRequest } from '../lib/latestRequest'
import { useLang } from '../lib/langContext'
import type { MessageKey } from '../lib/messages'

// Le snippet FTS entoure les termes trouvés de STX (\u0002) / ETX (\u0003).
// On les convertit en <mark> sans jamais injecter de HTML (XSS-safe).
function renderSnippet(s: string): ReactNode[] {
  const out: ReactNode[] = []
  let buf = ''
  let mark = false
  const flush = (k: number) => {
    if (!buf) return
    out.push(mark ? <mark key={k}>{buf}</mark> : <span key={k}>{buf}</span>)
    buf = ''
  }
  for (const ch of s) {
    if (ch === '\u0002') { flush(out.length); mark = true }
    else if (ch === '\u0003') { flush(out.length); mark = false }
    else buf += ch
  }
  flush(out.length)
  return out
}

type Props = {
  viewScope?: string
  scopeLabel?: string
  initialQuery?: string
  /** Nombre de documents dans la portée, si le parent le connaît déjà. */
  scopeCount?: number
  /** Le même compte pour TOUT le corpus, quand le bouton élargit la portée. */
  scopeCountAll?: number
  scopeLabelAll?: string
  /**
   * Remonte la requête ANTI-REBONDIE, pour que le parent la porte dans l'URL.
   *
   * On remonte `dq` et non `q` : c'est la requête réellement lancée. Remonter
   * `q` donnerait la MÊME URL finale — le parent remplace l'entrée au lieu de
   * l'empiler — mais une réécriture par caractère au lieu d'une par requête.
   * C'est donc une économie, pas un invariant, et aucun test ne la garde :
   * inventer une assertion sur un compteur d'appels certifierait une propriété
   * que personne ne constate.
   */
  onQueryChange?: (query: string) => void
}
type OpenedPassage = { name: string; ts: string | null; scope: string; title: string }

export function SearchPanel({
  viewScope = '', scopeLabel = 'Non classé', initialQuery = '', scopeCount,
  scopeCountAll, scopeLabelAll, onQueryChange,
}: Props) {
  const [q, setQ] = useState(initialQuery)
  const [dq, setDq] = useState(initialQuery)
  // Règle : le défaut est la recherche INTELLIGENTE.
  //
  // `semantic` n'est plus purement sémantique : il fusionne dense et
  // lexical par RRF, et rend mieux que le lexical seul y compris sur les termes
  // littéraux, là où l'exact était censé dominer. Le laisser en second choix envoyait l'utilisateur
  // vers le mode le plus faible pour son propre cas d'usage.
  const [mode, setMode] = useState<SearchMode>('semantic')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [opened, setOpened] = useState<OpenedPassage | null>(null)
  const [searchEverywhere, setSearchEverywhere] = useState(false)
  const [beginSearch, isCurrentSearch, invalidateSearch] = useLatestRequest()
  const { t } = useLang()

  /** Accorde un nombre. Le choix singulier/pluriel se fait ICI, en TypeScript :
   *  le catalogue ne porte jamais une phrase par combinaison d'état. */
  const compte = (
    base: 'search.count.passages' | 'search.count.meetings' | 'search.count.documents',
    n: number,
  ) =>
    t(`${base}.${n > 1 ? 'other' : 'one'}` as MessageKey, { n })

  useEffect(() => { setSearchEverywhere(false) }, [viewScope])

  const allProjects = viewScope === 'all' || searchEverywhere
  const activeScope = allProjects ? 'all' : viewScope
  // L'état initial doit décrire la portée RÉELLEMENT interrogée, pas celle que
  // le parent connaissait avant que l'utilisateur n'élargisse.
  const compteAffiche = allProjects ? scopeCountAll : scopeCount
  const porteeAffichee = allProjects ? (scopeLabelAll ?? scopeLabel) : scopeLabel

  useEffect(() => {
    const t = setTimeout(() => setDq(q), 250)
    return () => clearTimeout(t)
  }, [q])

  useEffect(() => { onQueryChange?.(dq) }, [dq, onQueryChange])

  useEffect(() => {
    if (!dq.trim()) {
      invalidateSearch(); setResults([]); setError(''); setLoading(false)
      return
    }
    const request = beginSearch()
    setLoading(true); setError('')
    search(dq, mode, activeScope)
      .then((value) => { if (isCurrentSearch(request)) setResults(value) })
      .catch((e) => {
        if (isCurrentSearch(request)) { setError(String(e)); setResults([]) }
      })
      .finally(() => { if (isCurrentSearch(request)) setLoading(false) })
    return () => invalidateSearch(request)
  }, [activeScope, beginSearch, dq, invalidateSearch, isCurrentSearch, mode])

  const groups = useMemo(() => {
    const byMeeting = new Map<string, SearchResult[]>()
    for (const result of results) {
      const key = result.meeting_id ?? result.document_id ?? `file:${result.name}`
      const current = byMeeting.get(key) ?? []
      current.push(result)
      byMeeting.set(key, current)
    }
    return [...byMeeting.entries()].map(([id, passages]) => ({ id, passages }))
  }, [results])

  /**
   * Ouvre un résultat AVEC la portée qu'il déclare.
   *
   * `activeScope` ne convient pas : c'est la portée de la REQUÊTE, et elle
   * vaut `all` dès que l'utilisateur a élargi. Le résultat, lui, vit dans un
   * projet précis — c'est celui-là qu'il faut pour le rouvrir.
   */
  const ouvrir = (result: SearchResult) => setOpened({
    name: result.name, ts: result.ts ?? null, scope: porteeDocument(result.project_id),
    title: result.title,
  })

  if (opened) {
    return (
      <TranscriptReader
        name={opened.name}
        documentScope={opened.scope}
        title={opened.title}
        initialTimestamp={opened.ts}
        onBack={() => setOpened(null)}
        backLabel={t('search.back')}
      />
    )
  }

  return (
    <section className="card query-card">
      <ScopeControl
        allProjects={allProjects}
        canToggle={viewScope !== 'all'}
        scopeLabel={scopeLabel}
        onToggle={() => setSearchEverywhere((value) => !value)}
        detail={t('search.scope.detail')}
      />

      <input
        className="search big" type="search" autoFocus
        aria-label={t('search.label')}
        placeholder={t(mode === 'fts' ? 'search.placeholder.exact' : 'search.placeholder.smart')}
        value={q} onChange={(e) => setQ(e.target.value)}
      />

      {/* Les deux modes ne sont PAS de même niveau. L'intelligente vient en
          premier et porte le défaut ; l'exacte reste offerte comme outil
          avancé — déterministe, explicable, et utilisable sans modèle
          d'embedding, donc c'est aussi le repli quand celui-ci est indisponible. */}
      <div className="seg" style={{ alignSelf: 'flex-start' }}>
        <button type="button" className={`seg-btn ${mode === 'semantic' ? 'on' : ''}`}
                onClick={() => setMode('semantic')}>{t('search.mode.smart')}</button>
        <button type="button" className={`seg-btn seg-btn-secondary ${mode === 'fts' ? 'on' : ''}`}
                onClick={() => setMode('fts')}>
          {t('search.mode.exact')}
          <span className="seg-btn-hint">{t('search.mode.exact.hint')}</span>
        </button>
      </div>

      {error && mode === 'semantic' ? (
        /* Le mode intelligent est le DÉFAUT : son indisponibilité ne doit pas
           laisser l'utilisateur devant un code HTTP. On nomme le problème et on
           tend l'outil qui, lui, ne dépend d'aucun modèle. Sans bascule
           automatique — rien ne change en silence dans ce projet. */
        <div className="evidence-state evidence-empty">
          <Icon name="error" />
          <div>
            <strong>{t('search.unavailable.title')}</strong>
            <p>{t('search.unavailable.detail')}</p>
            <p className="muted small">{t('search.unavailable.cause', { cause: error })}</p>
            <button type="button" className="btn" onClick={() => setMode('fts')}>
              {t('search.unavailable.action')}
            </button>
          </div>
        </div>
      ) : error ? (
        <p className="error"><Icon name="error" /> {error}</p>
      ) : !dq.trim() ? (
        /* Avant la première frappe, l'écran ne disait qu'une
           ligne d'aide. Il dit maintenant CE QU'IL Y A à chercher — et surtout
           quand il n'y a rien : « rien tapé » et « portée vide » étaient
           jusqu'ici indiscernables. Le compte vient du parent, qui charge déjà
           les projets : aucune requête supplémentaire. */
        <div className="search-intro">
          <p className="muted">{t(mode === 'fts' ? 'search.help.exact' : 'search.help.smart')}</p>
          {compteAffiche === 0 ? (
            <div className="evidence-state evidence-empty">
              <Icon name="search" />
              <div>
                <strong>{t('search.intro.empty.title')}</strong>
                <p>{t('search.intro.empty.detail', { portee: porteeAffichee })}</p>
              </div>
            </div>
          ) : compteAffiche !== undefined ? (
            <p className="muted small">
              {t('search.intro.scope', {
                documents: compte('search.count.documents', compteAffiche), portee: porteeAffichee,
              })}
            </p>
          ) : null}
        </div>
      ) : loading ? (
        <p className="muted">{t('search.running')}</p>
      ) : results.length === 0 ? (
        <div className="evidence-state evidence-empty">
          <Icon name="search" />
          <div>
            <strong>{t('search.empty.title')}</strong>
            <p>{t('search.empty.detail', { requete: dq })}</p>
          </div>
        </div>
      ) : (
        <>
          <p className="muted small">
            {t('search.count', {
              passages: compte('search.count.passages', results.length),
              reunions: compte('search.count.meetings', groups.length),
            })}
          </p>
          <div className="search-groups">
            {groups.map(({ id, passages }) => {
              const meeting = passages[0]
              const titre = displayDocumentTitle(meeting.title, meeting.source)
              return (
                <section className="search-meeting" key={id} aria-label={t('search.meeting.aria', { titre })}>
                  <header className="search-meeting-head">
                    <div>
                      <span className="action-scope">{t('search.meeting')}</span>
                      <h2>{titre}</h2>
                    </div>
                    <div className="badges">
                      {allProjects && meeting.project && <Badge tone="accent"><Icon name="folder" /> {meeting.project}</Badge>}
                      <Badge tone="neutral">{compte('search.count.passages', passages.length)}</Badge>
                    </div>
                  </header>
                  <ul className="search-passages">
                    {passages.map((result) => (
                      <li key={result.document_id ?? result.name} className="search-passage">
                        <button
                          type="button"
                          className="search-passage-open"
                          onClick={() => ouvrir(result)}
                          aria-label={result.ts
                            ? t('search.open.atTime.aria', { titre: result.title, horodatage: result.ts })
                            : t('search.open.aria', { titre: result.title })}
                        >
                          <span className="snippet">{renderSnippet(result.snippet)}</span>
                          <span className="search-document">{result.name}</span>
                        </button>
                        <div className="badges">
                          {result.origine === 'compte-rendu' && (
                            <Badge tone="llm">{t('common.generated')}</Badge>
                          )}
                          {result.ts && (
                            <button
                              type="button" className="timestamp-link"
                              onClick={() => ouvrir(result)}
                              aria-label={t('search.open.timestamp.aria', { horodatage: result.ts })}
                            >
                              <Icon name="clock" /> {result.ts}
                            </button>
                          )}
                          {result.language && <Badge tone="neutral">{result.language.toUpperCase()}</Badge>}
                          {!result.ts && result.duration && <Badge tone="neutral">{result.duration}</Badge>}
                          {result.diarization && <Badge tone="accent"><Icon name="users" /> {result.n_speakers ?? '?'}</Badge>}
                          {result.profiled && <Badge tone="llm"><Icon name="sparkles" /></Badge>}
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              )
            })}
          </div>
        </>
      )}
    </section>
  )
}
