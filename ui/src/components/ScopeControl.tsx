import { Icon } from './Icon'
import { useLang } from '../lib/langContext'

type Props = {
  allProjects: boolean
  canToggle: boolean
  scopeLabel: string
  onToggle: () => void
  detail: string
}

export function ScopeControl({ allProjects, canToggle, scopeLabel, onToggle, detail }: Props) {
  const { t } = useLang()
  const activeLabel = allProjects ? t('scope.allMine') : t('scope.project', { nom: scopeLabel })

  return (
    <div className={`query-scope ${allProjects ? 'query-scope-global' : ''}`}>
      <div className="query-scope-main">
        <span className="query-scope-label"><Icon name="folder" /> {t('scope.label', { portee: activeLabel })}</span>
        {canToggle && (
          <button type="button" className="btn sm" onClick={onToggle}>
            {allProjects ? t('scope.limitToProject', { nom: scopeLabel }) : t('scope.searchAllMine')}
          </button>
        )}
      </div>
      {allProjects && <p className="query-scope-detail"><Icon name="alert" /> {detail}</p>}
    </div>
  )
}
