import { useCallback, useEffect, useRef } from 'react'


/** Versionne les lectures asynchrones : seule la dernière peut publier son état. */
export function useLatestRequest() {
  const sequence = useRef(0)
  const begin = useCallback(() => ++sequence.current, [])
  const isCurrent = useCallback((request: number) => sequence.current === request, [])
  const invalidate = useCallback((request?: number) => {
    if (request === undefined || sequence.current === request) sequence.current += 1
  }, [])

  useEffect(() => invalidate, [invalidate])
  return [begin, isCurrent, invalidate] as const
}
