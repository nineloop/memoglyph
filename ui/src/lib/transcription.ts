/** Marge tolérée pour considérer qu'on est « en bas » de la zone de texte. */
export const STICK_MARGIN_PX = 24

export function isAtBottom(el: Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < STICK_MARGIN_PX
}

/**
 * Le VERDICT du contrôle, pas sa formulation.
 *
 * La fonction renvoyait une phrase française. Une interface bilingue en aurait
 * fait une décision qui change de forme avec la langue — or le refus d'un
 * fichier doit être exactement le même en FR et en EN. On sépare donc le motif
 * (ici) de son rendu (dans le composant, via le catalogue).
 */
export type MediaProblem =
  | { code: 'badExtension'; extension: string }
  | { code: 'noExtension' }
  | { code: 'empty' }
  | { code: 'tooLarge'; limite: number }

export function validateMediaFile(
  file: File,
  supportedExtensions: string[],
  maxUploadMb: number,
): MediaProblem | null {
  const dot = file.name.lastIndexOf('.')
  const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
  if (!supportedExtensions.includes(extension)) {
    return extension ? { code: 'badExtension', extension } : { code: 'noExtension' }
  }
  if (file.size === 0) return { code: 'empty' }
  if (file.size > maxUploadMb * 1024 * 1024) {
    return { code: 'tooLarge', limite: maxUploadMb }
  }
  return null
}

export function documentBody(content: string): string {
  return content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '')
}
