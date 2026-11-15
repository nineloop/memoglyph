import { useCallback, useEffect, useMemo, useState } from 'react'

import type { TrashItem } from '../api'
import {
  deleteTrashItem, emptyTrash, getTrash, restoreTrashItem,
} from '../api'
import { useLatestRequest } from '../lib/latestRequest'
import { displayDocumentTitle } from '../lib/meetings'
import { Icon } from './Icon'
import { EtatEchec } from '../design/primitives'
import { libelleProjet } from '../lib/libelleProjet'
import { useLang } from '../lib/langContext'
import { useConfirmer } from '../design/Confirmation'
import { phraseDErreur } from '../lib/erreurs'
import { pluralKey } from '../lib/i18n'
import type { Lang, Traduire } from '../lib/i18n'
import { formaterJourHeure } from '../lib/dates'


/** Titre lisible d'un élément de la corbeille, jamais le slug de son fichier. */
const titreDe = (item: TrashItem) => displayDocumentTitle(item.title, item.source)

type Props = {
  viewScope: string
  scopeLabel?: string
  onChanged?: () => void
}

function formatArchivedAt(value: string | null, t: Traduire, lang: Lang): string {
  if (!value) return t('trash.archivedAtUnknown')
  return formaterJourHeure(value, lang) ?? value
}

// L'UNITÉ passe par le catalogue (« o / Ko / Mo » ou « B / KB / MB ») ; le
// SÉPARATEUR décimal suit la langue — virgule en français, point en anglais.
// Sans ça, « 1,5 MB » s'affichait dans une interface anglaise.
function formatSize(bytes: number, t: Traduire, lang: Lang): string {
  const decimal = (n: number, digits: number): string => {
    const texte = n.toFixed(digits)
    return lang === 'fr' ? texte.replace('.', ',') : texte
  }
  if (bytes < 1024) return t('size.b', { valeur: bytes })
  const kib = bytes / 1024
  if (kib < 1024) return t('size.kb', { valeur: decimal(kib, kib < 10 ? 1 : 0) })
  const mib = kib / 1024
  return t('size.mb', { valeur: decimal(mib, mib < 10 ? 1 : 0) })
}

export function TrashPanel({
  viewScope, scopeLabel = 'la portée courante', onChanged,
}: Props) {
  const { t, lang } = useLang()
  const confirmer = useConfirmer()
  const [items, setItems] = useState<TrashItem[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  // `error` ne dit que l'échec d'une ACTION (restaurer, supprimer, vider) : la liste reste affichée.
  const [error, setError] = useState('')
  // L'échec du CHARGEMENT remplace la liste : « Aucun élément archivé » ne s'affiche que quand le chargement a ABOUTI.
  const [echecChargement, setEchecChargement] = useState(false)
  // L'échec qui SUIT le « Réessayer » de la personne reçoit le focus (le bouton se remonte) ; un autre ne le prend pas.
  const [apresReprise, setApresReprise] = useState(false)
  const [message, setMessage] = useState('')
  const [beginLoad, isCurrentLoad, invalidateLoad] = useLatestRequest()

  /** Recharge la corbeille ; rend le nombre d'éléments lus, ou `null` si la lecture échoue. */
  const load = useCallback(async (clearError = true): Promise<number | null> => {
    const request = beginLoad()
    setLoading(true)
    if (clearError) setError('')
    setEchecChargement(false)
    try {
      const result = await getTrash(viewScope)
      if (isCurrentLoad(request)) { setItems(result); setApresReprise(false) }
      return result.length
    } catch {
      // Une liste périmée ne sert ni à vider ni à supprimer : elle part avec l'échec.
      if (isCurrentLoad(request)) { setItems([]); setEchecChargement(true) }
      return null
    } finally {
      if (isCurrentLoad(request)) setLoading(false)
    }
  }, [beginLoad, isCurrentLoad, viewScope])

  const reessayer = () => { setApresReprise(true); void load() }

  useEffect(() => {
    load()
    return () => invalidateLoad()
  }, [invalidateLoad, load])

  const totalBytes = useMemo(
    () => items.reduce((total, item) => total + item.size, 0),
    [items],
  )

  async function restore(item: TrashItem) {
    setBusy(item.name); setError(''); setMessage('')
    try {
      const result = await restoreTrashItem(item.name, viewScope)
      const warning = result.warnings.length
        ? t('trash.restore.reserves', { list: result.warnings.join(' ; ') })
        : ''
      setMessage(t('trash.restore.done', { titre: titreDe(item), project: result.project, warning }))
      await load(false)
      onChanged?.()
    } catch (cause) {
      setError(phraseDErreur(t, lang, cause, t('trash.restore.failed')))
    } finally {
      setBusy(null)
    }
  }

  async function remove(item: TrashItem) {
    const confirmed = await confirmer({ titre: t('trash.delete.titre', { titre: titreDe(item) }), phrase: t('trash.delete.confirm'), libelleAction: t('trash.delete.action') })
    if (!confirmed) return
    setBusy(item.name); setError(''); setMessage('')
    try {
      const result = await deleteTrashItem(item.name, viewScope)
      const suffix = result.shared_artifacts_preserved ? t('trash.delete.sharedKept') : ''
      setMessage(t('trash.delete.done', { titre: titreDe(item), suffix }))
      await load(false)
      onChanged?.()
    } catch (cause) {
      setError(phraseDErreur(t, lang, cause, t('trash.delete.failed')))
      await load(false)
    } finally {
      setBusy(null)
    }
  }

  async function empty() {
    if (!items.length) return
    const label = viewScope === 'all'
      ? t(pluralKey('trash.empty.label.all', items.length), { n: items.length })
      : t(pluralKey('trash.empty.label.project', items.length), { n: items.length, scope: scopeLabel })
    const confirmed = await confirmer({
      titre: t('trash.empty.titre', { label }),
      phrase: t('trash.empty.confirm', { size: formatSize(totalBytes, t, lang) }),
      libelleAction: t('trash.empty.confirmAction'),
    })
    if (!confirmed) return
    const avant = items.length
    setBusy('__empty__'); setError(''); setMessage('')
    try {
      const result = await emptyTrash(viewScope)
      setMessage(
        t(pluralKey('trash.empty.done', result.removed), { n: result.removed, size: formatSize(result.freed_bytes, t, lang) }),
      )
      await load(false)
      onChanged?.()
    } catch (cause) {
      // « Nettoyage partiel » seulement si la corbeille a vraiment perdu des éléments ; sinon rien n'a été supprimé.
      const apres = await load(false)
      setError(phraseDErreur(t, lang, cause, t(apres !== null && apres >= avant ? 'trash.empty.failedNone' : 'trash.empty.failed')))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="trash-panel">
      <div className="trash-heading">
        <div>
          <span className="action-scope">{t('trash.eyebrow')}</span>
          <h2>{t('trash.title')}</h2>
          <p>{t('trash.subtitle')}</p>
        </div>
        <div className="trash-heading-actions">
          <button className="btn" onClick={() => load()} disabled={loading || Boolean(busy)}>
            <Icon name="refresh" /> {t('common.refresh')}
          </button>
          <button className="btn danger" onClick={empty} disabled={!items.length || Boolean(busy)}>
            <Icon name="trash" /> {busy === '__empty__' ? t('trash.emptying') : t('trash.empty.action')}
          </button>
        </div>
      </div>

      {error && <p className="error" role="alert"><Icon name="error" /> {error}</p>}
      {message && <p className="maintenance-message" role="status"><Icon name="check" /> {message}</p>}

      {loading && !items.length ? <p className="muted">{t('trash.loading')}</p>
        : echecChargement ? <EtatEchec onRetry={reessayer} focusRetry={apresReprise}>{t('trash.loadFailed')}</EtatEchec>
        : !items.length ? (
          <p className="diagnostic-ok"><Icon name="check" /> {t('trash.none')}</p>
        ) : (
          <div className="trash-list">
            {items.map((item) => (
              <article className="trash-item" key={item.name}>
                <div className="trash-item-main">
                  <div className="trash-item-title">
                    <Icon name={item.type === 'report' ? 'report' : 'archive'} />
                    <div>
                      <strong>{titreDe(item)}</strong>
                      <span>{item.origin}</span>
                    </div>
                  </div>
                  <div className="trash-item-meta">
                    <span>{item.type === 'report' ? t('common.docKind.report') : t('common.docKind.transcript')}</span>
                    <span>{libelleProjet(item.project || t('common.unclassified'), t)}</span>
                    <span>{formatArchivedAt(item.archived_at, t, lang)}</span>
                    <span>{formatSize(item.size, t, lang)}</span>
                  </div>
                </div>
                <div className="trash-item-actions">
                  <button className="btn" disabled={Boolean(busy)}
                          aria-label={t('trash.restore.aria', { titre: titreDe(item) })}
                          onClick={() => restore(item)}>
                    <Icon name="refresh" /> {busy === item.name ? t('trash.restoring') : t('trash.restore')}
                  </button>
                  <button className="btn danger" disabled={Boolean(busy)}
                          aria-label={t('trash.delete.aria', { titre: titreDe(item) })}
                          onClick={() => remove(item)}>
                    <Icon name="trash" /> {t('trash.delete')}
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
    </div>
  )
}
