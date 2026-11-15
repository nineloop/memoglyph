/**
 * `useTraitements` dit où en est la relève : « chargement » tant qu'aucune liste n'est arrivée, « ok » ensuite,
 * « échec » quand un tour échoue (la dernière liste reste alors disponible).
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import type { ListeTraitements } from '../api'
import { getTraitements } from '../api'
import { useTraitements } from '../lib/useTraitements'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, getTraitements: vi.fn() }
})

const LISTE: ListeTraitements = { traitements: [], actifs: 0, en_attente: 0 }

function enAttente<T>() {
  let resoudre!: (v: T) => void
  const promesse = new Promise<T>((ok) => { resoudre = ok })
  return { promesse, resoudre }
}

beforeEach(() => { vi.mocked(getTraitements).mockReset() })

it('tant qu’aucune liste n’est arrivée, l’état est « chargement », puis « ok »', async () => {
  const premiere = enAttente<ListeTraitements>()
  vi.mocked(getTraitements).mockReturnValue(premiere.promesse)
  const { result } = renderHook(() => useTraitements())

  expect(result.current.etat).toBe('chargement')
  expect(result.current.liste).toBeNull()
  await act(async () => { premiere.resoudre(LISTE) })
  await waitFor(() => expect(result.current.etat).toBe('ok'))
  expect(result.current.liste).toEqual(LISTE)
})

it('une première relève qui échoue donne « échec », sans liste', async () => {
  vi.mocked(getTraitements).mockRejectedValue(new Error('HTTP 503'))
  const { result } = renderHook(() => useTraitements())

  await waitFor(() => expect(result.current.etat).toBe('echec'))
  expect(result.current.liste).toBeNull()
})

it('« rafraichir » après un premier échec repasse par « chargement » puis « ok »', async () => {
  const seconde = enAttente<ListeTraitements>()
  vi.mocked(getTraitements).mockRejectedValueOnce(new Error('HTTP 503')).mockReturnValueOnce(seconde.promesse)
  const { result } = renderHook(() => useTraitements())
  await waitFor(() => expect(result.current.etat).toBe('echec'))

  act(() => { result.current.rafraichir() })
  expect(result.current.etat).toBe('chargement')
  await act(async () => { seconde.resoudre(LISTE) })
  await waitFor(() => expect(result.current.etat).toBe('ok'))
})

it('un échec après une liste reçue garde cette liste ; un succès suivant revient à « ok »', async () => {
  vi.mocked(getTraitements)
    .mockResolvedValueOnce(LISTE)
    .mockRejectedValueOnce(new Error('HTTP 503'))
    .mockResolvedValue(LISTE)
  const { result } = renderHook(() => useTraitements())
  await waitFor(() => expect(result.current.etat).toBe('ok'))

  act(() => { result.current.rafraichir() })
  await waitFor(() => expect(result.current.etat).toBe('echec'))
  expect(result.current.liste).toEqual(LISTE)            // la dernière liste reste affichable

  act(() => { result.current.rafraichir() })
  await waitFor(() => expect(result.current.etat).toBe('ok'))
})
