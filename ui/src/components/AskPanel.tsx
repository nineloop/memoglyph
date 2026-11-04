import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { RagAnswer, RagSource } from '../api'
import { usePartage } from '../lib/partage'
import { ErreurAvecCode, ModeleGratuitError, PORTEE_PARTAGES, ask, porteeDocument } from '../api'
import { TranscriptReader } from './TranscriptReader'
import { Icon } from './Icon'
import { Badge } from '../design/primitives'
import { ScopeControl } from './ScopeControl'
import { useLatestRequest } from '../lib/latestRequest'
import { libelleProjet } from '../lib/libelleProjet'
import { useLang } from '../lib/langContext'
import { traduireEtape } from '../lib/i18n'
import { pluralKey } from '../lib/i18n'
import type { MessageKey } from '../lib/messages'

type Props = {
  viewScope: string
  scopeLabel: string
  /** Nombre de documents dans la portée, si le parent le connaît déjà. */
  scopeCount?: number
  /** Le même compte pour TOUT le corpus, quand le bouton élargit la portée. */
  scopeCountAll?: number
  scopeLabelAll?: string
  /** Mène à la Bibliothèque, où se change le projet consulté. */
  onChangerPortee?: () => void
  /** Un modèle local joignable, tel que la configuration l'a rapporté. */
  llmAvailable?: boolean
}
type OpenedSource = { name: string; ts: string | null; scope: string; title: string }

/**
 * Trois façons d'entrer dans un corpus qu'on ne connaît pas encore.
 *
 * Ce sont des amorces d'INTERFACE, pas les gabarits de `core/templates.py` :
 * les recopier créerait deux sources de vérité qui dériveraient. Elles
 * remplissent le champ, sans l'envoyer — la question reste celle de
 * l'utilisateur.
 */
const AMORCES: MessageKey[] = [
  'ask.amorce.about',
  'ask.amorce.decisions',
  'ask.amorce.commitments',
]

export function AskPanel({
  viewScope, scopeLabel, scopeCount, scopeCountAll, scopeLabelAll, llmAvailable, onChangerPortee,
}: Props) {
  const { t, lang } = useLang()
  const [q, setQ] = useState('')
  const [res, setRes] = useState<RagAnswer | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [opened, setOpened] = useState<OpenedSource | null>(null)
  const [searchEverywhere, setSearchEverywhere] = useState(false)
  const [beginAsk, isCurrentAsk, invalidateAsk] = useLatestRequest()

  useEffect(() => {
    invalidateAsk(); setSearchEverywhere(false); setRes(null); setError(''); setLoading(false)
  }, [invalidateAsk, viewScope])

  const allProjects = viewScope === 'all' || searchEverywhere
  const { lectureSeule } = usePartage()
  // L'état initial doit décrire la portée RÉELLEMENT interrogée : le bouton
  // « chercher partout » change celle de la requête sans passer par le parent.
  const compteAffiche = allProjects ? scopeCountAll : scopeCount
  const porteeAffichee = allProjects ? (scopeLabelAll ?? scopeLabel) : scopeLabel
  const sourceNumbers = useMemo(
    () => new Set(res?.sources.map((source) => source.n) ?? []),
    [res],
  )
  const citedNumbers = useMemo(
    () => [...(res?.answer.matchAll(/\[(\d+)\]/g) ?? [])].map((match) => Number(match[1])),
    [res],
  )
  const missingCitations = citedNumbers.filter((number) => !sourceNumbers.has(number))
  const citedSourceNumbers = new Set(citedNumbers)
  const answerHasNoCitations = Boolean(res?.sources.length) && citedNumbers.length === 0

  async function run() {
    const question = q.trim()
    if (!question || loading) return
    const request = beginAsk()
    setLoading(true); setError(''); setRes(null)
    try {
      const value = await ask(
        question,
        allProjects ? { mode: 'all' }
          // Ce qui est partagé avec le compte : tout, ou un projet reçu (Q15).
          : lectureSeule
            ? (viewScope === PORTEE_PARTAGES ? { mode: 'shared' } : { mode: 'shared', project_id: viewScope })
            : { mode: 'project', project_id: viewScope },
      )
      if (isCurrentAsk(request)) setRes(value)
    } catch (e) {
      if (isCurrentAsk(request)) setError(
        e instanceof ModeleGratuitError ? t('job.step.error.freeModelRestricted')
          // Un code connu se traduit ; inconnu, la phrase du serveur reste (jamais la clé brute).
          : e instanceof ErreurAvecCode ? traduireEtape(lang, { code: e.code, params: {}, message: e.message })
            : e instanceof Error ? e.message : String(e))
    } finally {
      if (isCurrentAsk(request)) setLoading(false)
    }
  }

  /**
   * Ouvre une source AVEC la portée qu'elle déclare.
   *
   * `viewScope` ne convient pas : « chercher dans tous mes projets » est un
   * état LOCAL, et la réponse peut alors citer un document d'un autre projet.
   */
  const ouvrir = (source: RagSource) => setOpened({
    name: source.name, ts: source.ts, scope: porteeDocument(source.project_id),
    title: source.title,
  })

  function openSource(n: number) {
    const source = res?.sources.find((candidate) => candidate.n === n)
    if (source) ouvrir(source)
  }

  // Rend la réponse : **gras** en <strong>, citations [n] cliquables.
  function renderAnswer(text: string): ReactNode[] {
    const out: ReactNode[] = []
    const re = /\*\*(.+?)\*\*|\[(\d+)\]/g
    let last = 0
    let match: RegExpExecArray | null
    while ((match = re.exec(text)) !== null) {
      if (match.index > last) out.push(text.slice(last, match.index))
      if (match[1] !== undefined) {
        out.push(<strong key={out.length}>{match[1]}</strong>)
      } else {
        const number = Number(match[2])
        out.push(sourceNumbers.has(number)
          ? <button key={out.length} className="cite" onClick={() => openSource(number)} aria-label={t('ask.cite.open.aria', { n: number })}>[{number}]</button>
          : <span key={out.length} className="cite cite-missing" title={t('ask.cite.missing.title')}>[{number}]</span>)
      }
      last = re.lastIndex
    }
    if (last < text.length) out.push(text.slice(last))
    return out
  }

  if (opened) {
    return (
      <TranscriptReader
        name={opened.name}
        documentScope={opened.scope}
        title={opened.title}
        initialTimestamp={opened.ts}
        lancer
        onBack={() => setOpened(null)}
        backLabel={t('ask.back')}
      />
    )
  }

  return (
    <section className="card query-card">
      <ScopeControl
        allProjects={allProjects}
        viewScope={viewScope} onChangerPortee={onChangerPortee}
        canToggle={viewScope !== 'all'}
        scopeLabel={scopeLabel}
        onToggle={() => setSearchEverywhere((value) => !value)}
        detail={t('ask.scope.detail')}
      />

      <div className="ask-bar">
        <textarea
          className="ask-input" rows={2} autoFocus
          placeholder={t('ask.placeholder')}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) run() }}
        />
        <button className="primary llm" onClick={run} disabled={!q.trim() || loading}>
          {loading ? t('ask.submitting') : t('ask.submit')}
        </button>
      </div>

      {error ? <p className="error"><Icon name="error" /> {error}</p>
        : loading ? <p className="muted">{t('ask.writing')}</p>
        : !res ? (
          /* Deux manques comblés ici, tous deux gratuits :
             l'indisponibilité du modèle se disait APRÈS quarante mots tapés, et
             rien ne distinguait une portée vide d'un champ vide. */
          <div className="ask-intro">
            <p className="muted">
              {t('ask.intro.pre')}<span className="kbd">{t('ask.intro.kbd')}</span>{t('ask.intro.post')}
            </p>
            {llmAvailable === false && (
              <p className="warn small">
                <Icon name="alert" /> {t('ask.noLocalModel')}
              </p>
            )}
            {compteAffiche === 0 ? (
              <div className="evidence-state evidence-empty">
                <Icon name="search" />
                <div>
                  <strong>{t('ask.empty.title')}</strong>
                  <p>{t('ask.empty.detail', { portee: porteeAffichee })}</p>
                </div>
              </div>
            ) : (
              <>
                {compteAffiche !== undefined && (
                  <p className="muted small">
                    {t(pluralKey('ask.scopeFeeds', compteAffiche), { n: compteAffiche, portee: porteeAffichee })}
                  </p>
                )}
                <div className="ask-amorces">
                  <span className="muted small">{t('ask.amorces.label')}</span>
                  {AMORCES.map((amorce) => (
                    <button key={amorce} type="button" className="btn sm" onClick={() => setQ(t(amorce))}>
                      {t(amorce)}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        )
        : res.sources.length === 0 ? (
          <div className="evidence-state evidence-empty" role="status">
            <Icon name="alert" />
            <div>
              <strong>{t('ask.noEvidence.title')}</strong>
              <p>{res.answer || t('ask.noEvidence.detail')}</p>
            </div>
          </div>
        ) : (
          <div className="rag-result">
            <section className="answer-panel" aria-labelledby="answer-title">
              <div className="answer-heading">
                <div><span className="action-scope">{t('ask.answer.eyebrow')}</span><h2 id="answer-title">{t('ask.answer.title')}</h2></div>
                <span className="answer-caveat"><Icon name="alert" /> {t('ask.answer.caveat')}</span>
              </div>
              <div className="answer">{renderAnswer(res.answer)}</div>
              {!!res.passages_ecartes && (
                <p className="evidence-warning" role="status">
                  <Icon name="alert" /> {t('ask.answer.ultraKeptLocal', { n: String(res.passages_ecartes) })}
                </p>
              )}
              {(missingCitations.length > 0 || answerHasNoCitations) && (
                <p className="evidence-warning" role="status">
                  <Icon name="alert" />
                  {answerHasNoCitations
                    ? t('ask.answer.noCitations')
                    : t(missingCitations.length > 1 ? 'ask.answer.missingOther' : 'ask.answer.missingOne',
                        { refs: missingCitations.map((n) => `[${n}]`).join(', ') })}
                </p>
              )}
            </section>

            <aside className="sources-panel" aria-labelledby="sources-title">
              <div className="sources-heading">
                <div><span className="action-scope">{t('ask.sources.eyebrow')}</span><h2 id="sources-title">{t('ask.sources.title')}</h2></div>
                <Badge tone="neutral">{res.sources.length}</Badge>
              </div>
              <p className="sources-note">{t('ask.sources.note')}</p>
              <ol className="source-list">
                {res.sources.map((source) => (
                  <li key={source.n} className="source-card">
                    <button
                      type="button" className="source-open"
                      onClick={() => ouvrir(source)}
                      aria-label={t('ask.source.open.aria', { n: source.n })}
                    >
                      <span className="source-title">
                        <span className="cite-n">[{source.n}]</span> {source.title}
                        {source.origine === 'compte-rendu' && <Badge tone="llm">{t('common.generated')}</Badge>}
                        <span className={`source-status ${citedSourceNumbers.has(source.n) ? 'cited' : ''}`}>
                          {citedSourceNumbers.has(source.n) ? t('ask.source.cited') : t('ask.source.notCited')}
                        </span>
                      </span>
                      <span className="snippet">{source.excerpt}</span>
                    </button>
                    <div className="badges">
                      {source.ts && (
                        <button
                          type="button" className="timestamp-link"
                          onClick={() => ouvrir(source)}
                          aria-label={t('ask.source.goto.aria', { ts: source.ts, n: source.n })}
                        >
                          <Icon name="clock" /> {source.ts}
                        </button>
                      )}
                      {allProjects && source.project && <Badge tone="accent"><Icon name="folder" /> {libelleProjet(source.project, t)}</Badge>}
                    </div>
                  </li>
                ))}
              </ol>
            </aside>
          </div>
        )}
    </section>
  )
}
