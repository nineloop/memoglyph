import type { ReactNode } from 'react'
import { useLang } from '../lib/langContext'
import type { Traduire } from '../lib/i18n'

function safeHref(value: string): string | null {
  return /^(https?:\/\/|mailto:|\/)/i.test(value.trim()) ? value.trim() : null
}

function inline(
  text: string,
  prefix: string,
  t: Traduire,
  onTimestamp?: (timestamp: string) => void,
): ReactNode[] {
  const token = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\[source (?:none|\d{1,2}:\d{2}(?::\d{2})?)\])/g
  const out: ReactNode[] = []
  let cursor = 0
  for (const match of text.matchAll(token)) {
    const index = match.index ?? 0
    if (index > cursor) out.push(text.slice(cursor, index))
    const value = match[0]
    const key = `${prefix}-${index}`
    const source = /^\[source (none|\d{1,2}:\d{2}(?::\d{2})?)\]$/.exec(value)
    if (source && source[1] !== 'none' && onTimestamp) {
      out.push(
        <button
          key={key}
          type="button"
          className="source-chip"
          onClick={() => onTimestamp(source[1])}
          aria-label={t('md.openSource.aria', { ts: source[1] })}
        >
          {source[1]}
        </button>,
      )
    } else if (source) {
      out.push(<code key={key}>{source[1] === 'none' ? t('md.sourceMissing') : source[1]}</code>)
    } else if (value.startsWith('**')) {
      out.push(<strong key={key}>{value.slice(2, -2)}</strong>)
    } else if (value.startsWith('`')) {
      out.push(<code key={key}>{value.slice(1, -1)}</code>)
    } else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(value)
      const href = link ? safeHref(link[2]) : null
      out.push(href
        ? <a key={key} href={href} target="_blank" rel="noreferrer">{link![1]}</a>
        : <span key={key}>{link?.[1] ?? value}</span>)
    }
    cursor = index + value.length
  }
  if (cursor < text.length) out.push(text.slice(cursor))
  return out
}

function startsBlock(line: string): boolean {
  return /^(#{1,6}\s|```|\s*[-*+]\s+|\s*\d+\.\s+|>\s?|---+$)/.test(line)
}

export function MarkdownView({
  content,
  onTimestamp,
}: {
  content: string
  onTimestamp?: (timestamp: string) => void
}) {
  const { t } = useLang()
  const lines = content.replace(/\r\n?/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index]
    if (!line.trim()) { index += 1; continue }

    if (line.trim().startsWith('<!--')) {
      while (index < lines.length && !lines[index].includes('-->')) index += 1
      if (index < lines.length) index += 1
      continue
    }

    if (line.startsWith('```')) {
      const language = line.slice(3).trim()
      const code: string[] = []
      index += 1
      while (index < lines.length && !lines[index].startsWith('```')) code.push(lines[index++])
      if (index < lines.length) index += 1
      blocks.push(<pre key={`code-${index}`}><code data-language={language || undefined}>{code.join('\n')}</code></pre>)
      continue
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(line)
    if (heading) {
      const level = heading[1].length
      const children = inline(heading[2], `h-${index}`, t, onTimestamp)
      if (level === 1) blocks.push(<h1 key={`h-${index}`}>{children}</h1>)
      else if (level === 2) blocks.push(<h2 key={`h-${index}`}>{children}</h2>)
      else if (level === 3) blocks.push(<h3 key={`h-${index}`}>{children}</h3>)
      else blocks.push(<h4 key={`h-${index}`}>{children}</h4>)
      index += 1
      continue
    }

    if (/^---+$/.test(line.trim())) {
      blocks.push(<hr key={`hr-${index}`} />)
      index += 1
      continue
    }

    const unordered = /^\s*[-*+]\s+/.test(line)
    const ordered = /^\s*\d+\.\s+/.test(line)
    if (unordered || ordered) {
      const items: ReactNode[] = []
      const pattern = unordered ? /^\s*[-*+]\s+(.+)$/ : /^\s*\d+\.\s+(.+)$/
      while (index < lines.length) {
        const item = pattern.exec(lines[index])
        if (!item) break
        items.push(<li key={`li-${index}`}>{inline(item[1], `li-${index}`, t, onTimestamp)}</li>)
        index += 1
      }
      blocks.push(unordered ? <ul key={`ul-${index}`}>{items}</ul> : <ol key={`ol-${index}`}>{items}</ol>)
      continue
    }

    if (line.startsWith('>')) {
      const quote: string[] = []
      while (index < lines.length && lines[index].startsWith('>')) {
        quote.push(lines[index].replace(/^>\s?/, ''))
        index += 1
      }
      blocks.push(<blockquote key={`quote-${index}`}>{inline(quote.join(' '), `quote-${index}`, t, onTimestamp)}</blockquote>)
      continue
    }

    const paragraph = [line.trim()]
    index += 1
    while (index < lines.length && lines[index].trim() && !startsBlock(lines[index])) {
      paragraph.push(lines[index].trim())
      index += 1
    }
    blocks.push(<p key={`p-${index}`}>{inline(paragraph.join(' '), `p-${index}`, t, onTimestamp)}</p>)
  }

  // `dir="auto"` plutôt qu'une direction imposée : le navigateur déduit le sens
  // du PREMIER CARACTÈRE FORT de chaque bloc. Un corpus réellement mixte —
  // c'est le cas ici, français et darija dans un même message — s'affiche donc
  // paragraphe par paragraphe dans le bon sens, sans qu'on ait à détecter quoi
  // que ce soit ni à demander sa langue à l'utilisateur.
  return <article className="markdown-view" dir="auto">{blocks}</article>
}
