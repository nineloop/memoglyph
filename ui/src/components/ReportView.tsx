import { MarkdownView } from './MarkdownView'
import { useLang } from '../lib/langContext'
import type { MessageKey } from '../lib/messages'

// Marqueurs écrits par le serveur (`core/report_citations.py`).
const EVIDENCE_MARKER = '<!-- memoglyph:evidence -->'
const NOTICE_MARKER = '<!-- memoglyph:generated-notices -->'

function findMarker(body: string, marker: string): { index: number; length: number } {
  const idx = body.indexOf(marker)
  return idx >= 0 ? { index: idx, length: marker.length } : { index: -1, length: 0 }
}

// Genres connus du backend (`core/templates`). La clé de libellé i18n en est
// dérivée ; un genre inconnu retombe sur son code humanisé plutôt que de
// masquer une valeur que l'UI ne traduit pas encore.
const GENRES_CONNUS = new Set([
  'generique', 'analyse_sportive', 'interview', 'projet', 'steerco',
  'entretien_embauche', 'webinar', 'keynote', 'town_hall', 'internal_briefing',
])

function withoutFrontmatter(content: string): string {
  const normalized = content.replace(/\r\n?/g, '\n')
  if (!normalized.startsWith('---\n')) return normalized
  const end = normalized.indexOf('\n---\n', 4)
  return end >= 0 ? normalized.slice(end + 5).trimStart() : normalized
}

// Le générateur ouvre le compte-rendu par un H1 (« Compte-rendu — <slug> »,
// « Report — <slug> ») qui répète, en moins lisible, le titre de la réunion déjà
// affiché dans l'en-tête. Il n'est retiré de l'AFFICHAGE que s'il est le premier
// bloc et que du contenu le suit : un H1 plus bas reste, et le `.md` téléchargé
// garde le sien.
function withoutLeadingTitle(markdown: string): string {
  const lines = markdown.replace(/^\s*\n/, '').split('\n')
  if (!/^# (?!#)/.test(lines[0] ?? '')) return markdown
  const rest = lines.slice(1).join('\n').trimStart()
  return rest ? rest : markdown
}

export function ReportView({
  content,
  genre,
  model,
  onTimestamp,
}: {
  content: string
  genre?: string | null
  model?: string | null
  onTimestamp?: (timestamp: string) => void
}) {
  const { t } = useLang()
  const body = withoutLeadingTitle(withoutFrontmatter(content))
  const evMatch = findMarker(body, EVIDENCE_MARKER)
  const label = !genre
    ? null
    : GENRES_CONNUS.has(genre)
      ? t(`report.genre.${genre}` as MessageKey)
      : genre.replaceAll('_', ' ')

  if (evMatch.index < 0) {
    return (
      <div className="report-view">
        {label && <span className="report-genre">{label}</span>}
        {model && (
          <span className="report-model" title={t('report.model.title')}>
            {t('report.model.label')} · <code>{model}</code>
          </span>
        )}
        <MarkdownView content={body} />
      </div>
    )
  }

  const editorial = body.slice(0, evMatch.index).trim()
  const afterMarker = body.slice(evMatch.index + evMatch.length)
  const noticeMatch = findMarker(afterMarker, NOTICE_MARKER)
  const evidence = (
    noticeMatch.index >= 0 ? afterMarker.slice(0, noticeMatch.index) : afterMarker
  ).trim()
  const notices = noticeMatch.index >= 0
    ? afterMarker.slice(noticeMatch.index + noticeMatch.length).trim()
    : ''
  const sourceCount = (evidence.match(/^### claim-\d+/gm) ?? []).length

  return (
    <div className="report-view">
      {label && <span className="report-genre">{label}</span>}
      {model && (
        <span className="report-model" title={t('report.model.title')}>
          {t('report.model.label')} · <code>{model}</code>
        </span>
      )}
      <MarkdownView content={editorial} onTimestamp={onTimestamp} />
      <details className="report-evidence">
        <summary>
          {t('report.evidence.summary')}
          {sourceCount > 0 && <span>{sourceCount}</span>}
        </summary>
        <MarkdownView content={evidence} />
      </details>
      {notices && <MarkdownView content={notices} />}
    </div>
  )
}
