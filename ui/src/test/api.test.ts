import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  archiveMeeting,
  ask,
  deleteTrashItem,
  emptyTrash,
  getArchivePlan,
  getFinopsExecution,
  getUsageRollup,
  getUsageExecutions,
  getUsageExecutionDetail,
  getFinopsSummary,
  getLibrary,
  getMeetings,
  getTrash,
  getTranscript,
  moveMeeting,
  moveTranscript,
  restoreTrashItem,
  streamJob,
  submitTranscribe,
} from '../api'


afterEach(() => {
  vi.unstubAllGlobals()
})


describe('client API', () => {
  it('construit les filtres de bibliothèque sans envoyer les valeurs absentes', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getLibrary({
      q: 'climat',
      sort: 'title',
      order: 'asc',
      language: 'fr',
      diarized: true,
      profiled: false,
      viewScope: '01KY243SVP5GA31F6KSBYXEAHR',
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/library?q=climat&sort=title&order=asc&language=fr&diarized=true&view_scope=01KY243SVP5GA31F6KSBYXEAHR',
    )
  })

  it('inclut les options et le fichier dans la soumission multipart', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ job_id: 'job-1' }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['audio'], 'sample.mp3', { type: 'audio/mpeg' })

    await expect(submitTranscribe(file, {
      engine: 'whisper',
      language: 'fr',
      diarize: true,
      profile: false,
      timestamps: true,
      destinationProjectId: '01KY243SVP5GA31F6KSBYXEAHR',
    })).resolves.toBe('job-1')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const form = init.body as FormData
    expect(url).toBe('/api/transcribe')
    expect(init.method).toBe('POST')
    expect(form.get('file')).toBe(file)
    expect(form.get('engine')).toBe('whisper')
    expect(form.get('diarize')).toBe('true')
    expect(form.get('profile')).toBe('false')
    expect(form.get('destination_project_id')).toBe('01KY243SVP5GA31F6KSBYXEAHR')
  })

  it('nomme explicitement la destination lors du classement', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ name: 'note.md', project: 'Client', project_id: 'pid' }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await moveTranscript('note.md', '01KY243SVQMK79ZY6TVJW2Q3Y0', 'pid-source')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/transcript/note.md/project?view_scope=pid-source', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ destination_project_id: '01KY243SVQMK79ZY6TVJW2Q3Y0' }),
    })
  })

  it('adresse la bibliothèque par réunion et nomme la portée des actions', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ archived_meeting: 'mid', documents: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await getMeetings({ diarized: true, viewScope: 'pid' })
    await moveMeeting('mid', 'target-pid', 'pid-source')
    await archiveMeeting('mid', 'pid-source')

    expect(fetchMock.mock.calls[0][0]).toBe('/api/meetings?diarized=true&view_scope=pid')
    expect(fetchMock.mock.calls[1]).toEqual([
      '/api/meetings/mid/project?view_scope=pid-source',
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ destination_project_id: 'target-pid' }),
      },
    ])
    expect(fetchMock.mock.calls[2]).toEqual([
      '/api/meetings/mid?view_scope=pid-source', { method: 'DELETE' },
    ])
  })

  it('envoie toujours une portée RAG structurée', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ answer: 'ok', sources: [] }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await ask('Question', { mode: 'project', project_id: 'pid' })

    expect(fetchMock).toHaveBeenCalledWith('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: 'Question', scope: { mode: 'project', project_id: 'pid' },
        // L'Assistant transporte le provider LLM choisi, comme la transcription
        // et le compte-rendu : plus de forçage muet.
        //
        // Le défaut est `mlx` (le local tourne sur MLX, voir
        // `lib/llmProvider.ts`). Ce test épingle le REPLI d'un stockage vide ;
        // un choix déjà persisté n'est pas écrasé par `getLlmProvider`.
        llm_provider: 'mlx',
      }),
    })
  })

  it('borne toutes les opérations de corbeille à la portée explicite', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({ documents: [] }), { status: 200 }),
    ))
    vi.stubGlobal('fetch', fetchMock)

    await getArchivePlan('note ancienne.md', 'project-1')
    await getTrash('project-1')
    await restoreTrashItem('note.1.md', 'project-1')
    await deleteTrashItem('note.1.md', 'project-1')
    await emptyTrash('project-1')

    expect(fetchMock.mock.calls).toEqual([
      ['/api/transcript/note%20ancienne.md/archive-plan?view_scope=project-1'],
      ['/api/trash?view_scope=project-1'],
      ['/api/trash/note.1.md/restore?view_scope=project-1', { method: 'POST' }],
      ['/api/trash/note.1.md?view_scope=project-1', { method: 'DELETE' }],
      ['/api/trash?view_scope=project-1', { method: 'DELETE' }],
    ])
  })

  it('remonte le corps des erreurs HTTP', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response('introuvable', { status: 404 }),
    ))

    await expect(getTranscript('missing.md', 'all')).rejects.toThrow(
      'HTTP 404: introuvable',
    )
  })

  it('ferme le flux SSE après le résultat final', () => {
    class FakeEventSource {
      static instance: FakeEventSource
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      close = vi.fn()
      url: string

      constructor(url: string) {
        this.url = url
        FakeEventSource.instance = this
      }
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    const onProgress = vi.fn()
    const onDone = vi.fn()

    streamJob('abc', { onProgress, onDone })
    const source = FakeEventSource.instance
    source.onmessage?.({
      data: JSON.stringify({
        type: 'progress', progress: 0.4, message: 'Identifying speakers…',
        code: 'step.diarization', params: {},
      }),
    } as MessageEvent)
    source.onmessage?.({
      data: JSON.stringify({ type: 'done', result: { name: 'x.md', content: 'ok' } }),
    } as MessageEvent)

    expect(source.url).toBe('/api/jobs/abc/events')
    expect(onProgress).toHaveBeenCalledWith(0.4, 'Identifying speakers…', {
      code: 'step.diarization', params: {}, message: 'Identifying speakers…',
    })
    expect(onDone).toHaveBeenCalledWith({ name: 'x.md', content: 'ok' })
    expect(source.close).toHaveBeenCalledOnce()
  })

  it('récupère le résultat par le statut après une coupure SSE', async () => {
    class FakeEventSource {
      static instance: FakeEventSource
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onopen: (() => void) | null = null
      close = vi.fn()

      constructor(_url: string) {
        FakeEventSource.instance = this
      }
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        id: 'abc', status: 'done', progress: 1, message: 'Fini',
        result: { name: 'x.md', content: 'récupéré' }, error: null,
      }), { status: 200 }),
    ))
    const onDone = vi.fn()
    const onError = vi.fn()

    streamJob('abc', { onDone, onError })
    FakeEventSource.instance.onerror?.()

    await vi.waitFor(() => expect(onDone).toHaveBeenCalledWith({
      name: 'x.md', content: 'récupéré',
    }))
    expect(onError).not.toHaveBeenCalled()
    expect(FakeEventSource.instance.close).toHaveBeenCalledOnce()
  })

  it('garde la reconnexion native ouverte quand le job tourne encore', async () => {
    class FakeEventSource {
      static instance: FakeEventSource
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onopen: (() => void) | null = null
      close = vi.fn()

      constructor(_url: string) {
        FakeEventSource.instance = this
      }
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        id: 'abc', status: 'running', progress: 0.6,
        message: 'Profiling with qwen…',
        message_code: 'step.profiling', message_params: { model: 'qwen' },
        result: null, error: null,
      }), { status: 200 }),
    ))
    const onProgress = vi.fn()
    const onError = vi.fn()

    const stop = streamJob('abc', { onProgress, onError })
    FakeEventSource.instance.onerror?.()

    // Le code doit survivre à la RECONNEXION, pas seulement au flux : c'est
    // par `snapshot()` qu'un client revient après une coupure.
    await vi.waitFor(() =>
      expect(onProgress).toHaveBeenCalledWith(0.6, 'Profiling with qwen…', {
        code: 'step.profiling',
        params: { model: 'qwen' },
        message: 'Profiling with qwen…',
      }),
    )
    expect(onError).not.toHaveBeenCalled()
    expect(FakeEventSource.instance.close).not.toHaveBeenCalled()
    stop()
  })
})

describe('FinOps read API — scope propagé, encodage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('getFinopsSummary porte le view_scope et encode le nom', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ available: false }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await getFinopsSummary('a b.md', '01KPROJECT')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/transcript/a%20b.md/finops?view_scope=01KPROJECT')
  })

  it('getFinopsExecution vise l’exécution scopée au document', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ available: true, calls: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await getFinopsExecution('r.md', 'EX9', 'all')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/transcript/r.md/finops/execution/EX9?view_scope=all')
  })
})

describe('Usage dashboard API — scope + filtres + bornes', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('getUsageRollup porte le scope, le group_by et les filtres non vides', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ group_by: 'day', scope: '01PROJ', rows: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await getUsageRollup('01PROJ', 'provider', { start: '2026-08-01', stage: '' })
    // stage vide OMIS ; view_scope + group_by + start présents.
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/usage/rollup?view_scope=01PROJ&group_by=provider&start=2026-08-01')
  })

  it('getUsageRollup en global n’envoie pas de projet mais reste explicite', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ group_by: 'cost_basis', scope: 'all', rows: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await getUsageRollup('all', 'cost_basis')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/usage/rollup?view_scope=all&group_by=cost_basis')
  })

  it('getUsageExecutions borne la liste (limit/offset) et porte les filtres', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ executions: [], limit: 10, offset: 0, has_more: false, scope: '01PROJ' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await getUsageExecutions('01PROJ', { provider: 'openai' }, 10, 20)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/usage/executions?view_scope=01PROJ&provider=openai&limit=10&offset=20')
  })

  it('getUsageExecutionDetail scope l’exécution', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ available: true, calls: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await getUsageExecutionDetail('01PROJ', 'EX1')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/usage/executions/EX1?view_scope=01PROJ')
  })
})
