import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useProjectCodingStore } from '../src/stores/project-coding'

describe('project coding store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads OpenCode models and files for the selected project', async () => {
    api.get
      .mockResolvedValueOnce({ data: {
        items: [{ key: 'provider/model', variants: ['low', 'high'] }],
        analysisModelKey: 'provider/model',
        analysisModelVariant: 'high',
        executionModelKey: 'provider/model',
        executionModelVariant: null
      } })
      .mockResolvedValueOnce({ data: { path: '.', items: [{ path: 'src', name: 'src', type: 'directory' }] } })
    const store = useProjectCodingStore()
    store.selectProject('project-1')

    await store.loadModels()
    await store.loadFiles('.')

    expect(api.get).toHaveBeenNthCalledWith(1, '/projects/project-1/coding/models', undefined)
    expect(api.get).toHaveBeenNthCalledWith(2, '/projects/project-1/coding/files', { params: { path: '.' } })
    expect(store.models).toEqual([{ key: 'provider/model', variants: ['low', 'high'] }])
    expect(store.analysisModelVariant).toBe('high')
    expect(store.executionModelVariant).toBe(null)
    expect(store.files[0].path).toBe('src')
  })

  it('sends exact model, queue, and permission payloads', async () => {
    api.put.mockResolvedValue({ data: {
      analysisModelKey: 'a/model',
      analysisModelVariant: 'high',
      executionModelKey: 'b/model',
      executionModelVariant: null
    } })
    api.post.mockResolvedValue({ data: { items: [], queuePauseReason: null } })
    api.get.mockResolvedValue({ data: { items: [], queuePauseReason: null } })
    const store = useProjectCodingStore()
    store.selectProject('project-1')

    await store.saveModels('a/model', 'high', 'b/model', null)
    await store.enqueue(['plan-1'])
    await store.replyPermission('execution-1', 'once')

    expect(api.put).toHaveBeenCalledWith('/projects/project-1/coding/models', {
      analysisModelKey: 'a/model',
      analysisModelVariant: 'high',
      executionModelKey: 'b/model',
      executionModelVariant: null
    })
    expect(api.post).toHaveBeenCalledWith('/projects/project-1/coding/plans/enqueue', { planIds: ['plan-1'] })
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.post).toHaveBeenCalledWith(
      '/projects/project-1/coding/executions/execution-1/permission',
      { response: 'once' }
    )
  })

  it('discards a response after the selected project changes', async () => {
    let resolveRequest
    api.get.mockReturnValue(new Promise((resolve) => { resolveRequest = resolve }))
    const store = useProjectCodingStore()
    store.selectProject('project-1')
    const request = store.loadModels()

    store.selectProject('project-2')
    resolveRequest({ data: { items: [{ key: 'stale/model' }], analysisModelKey: null, executionModelKey: null } })
    await request

    expect(store.projectId).toBe('project-2')
    expect(store.models).toEqual([])
  })

  it('discards a response after the binding changes in the same project', async () => {
    let resolveRequest
    api.get.mockReturnValue(new Promise((resolve) => { resolveRequest = resolve }))
    const store = useProjectCodingStore()
    store.selectProject('project-1')
    const request = store.loadModels()

    store.resetProject('project-1')
    resolveRequest({ data: { items: [{ key: 'stale/model' }], analysisModelKey: null, executionModelKey: null } })
    await request

    expect(store.projectId).toBe('project-1')
    expect(store.models).toEqual([])
    expect(store.pending).toBe(0)
  })

  it('discards aggregate refresh state after the binding changes', async () => {
    let rejectRequest
    api.get
      .mockReturnValueOnce(new Promise((resolve, reject) => { rejectRequest = reject }))
      .mockResolvedValue({ data: { items: [], path: '.', queuePauseReason: null } })
    const store = useProjectCodingStore()
    store.selectProject('project-1')
    const refresh = store.refresh().catch(() => {})

    store.resetProject('project-1')
    rejectRequest(new Error('stale failure'))
    await refresh

    expect(store.loaded).toBe(false)
    expect(store.loadFailed).toBe(false)
  })

  it('does not let an older model poll overwrite a saved selection', async () => {
    let resolveRequest
    api.get.mockReturnValue(new Promise((resolve) => { resolveRequest = resolve }))
    api.put.mockResolvedValue({ data: {
      analysisModelKey: 'saved/analyze',
      analysisModelVariant: 'high',
      executionModelKey: 'saved/build',
      executionModelVariant: 'max'
    } })
    const store = useProjectCodingStore()
    store.selectProject('project-1')
    const poll = store.loadModels()

    await store.saveModels('saved/analyze', 'high', 'saved/build', 'max')
    resolveRequest({ data: {
      items: [],
      analysisModelKey: 'stale/analyze',
      analysisModelVariant: 'low',
      executionModelKey: 'stale/build',
      executionModelVariant: null
    } })
    await poll

    expect(store.analysisModelKey).toBe('saved/analyze')
    expect(store.analysisModelVariant).toBe('high')
    expect(store.executionModelKey).toBe('saved/build')
    expect(store.executionModelVariant).toBe('max')
  })
})
