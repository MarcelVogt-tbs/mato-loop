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
      .mockResolvedValueOnce({ data: { items: [{ key: 'provider/model' }], analysisModelKey: null, executionModelKey: null } })
      .mockResolvedValueOnce({ data: { path: '.', items: [{ path: 'src', name: 'src', type: 'directory' }] } })
    const store = useProjectCodingStore()
    store.selectProject('project-1')

    await store.loadModels()
    await store.loadFiles('.')

    expect(api.get).toHaveBeenNthCalledWith(1, '/projects/project-1/coding/models', undefined)
    expect(api.get).toHaveBeenNthCalledWith(2, '/projects/project-1/coding/files', { params: { path: '.' } })
    expect(store.models).toEqual([{ key: 'provider/model' }])
    expect(store.files[0].path).toBe('src')
  })

  it('sends exact model, queue, and permission payloads', async () => {
    api.put.mockResolvedValue({ data: { analysisModelKey: 'a/model', executionModelKey: 'b/model' } })
    api.post.mockResolvedValue({ data: { items: [], queuePauseReason: null } })
    api.get.mockResolvedValue({ data: { items: [], queuePauseReason: null } })
    const store = useProjectCodingStore()
    store.selectProject('project-1')

    await store.saveModels('a/model', 'b/model')
    await store.enqueue(['plan-1'])
    await store.replyPermission('execution-1', 'once')

    expect(api.put).toHaveBeenCalledWith('/projects/project-1/coding/models', {
      analysisModelKey: 'a/model', executionModelKey: 'b/model'
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

  it('does not let an older model poll overwrite a saved selection', async () => {
    let resolveRequest
    api.get.mockReturnValue(new Promise((resolve) => { resolveRequest = resolve }))
    api.put.mockResolvedValue({ data: {
      analysisModelKey: 'saved/analyze',
      executionModelKey: 'saved/build'
    } })
    const store = useProjectCodingStore()
    store.selectProject('project-1')
    const poll = store.loadModels()

    await store.saveModels('saved/analyze', 'saved/build')
    resolveRequest({ data: {
      items: [],
      analysisModelKey: 'stale/analyze',
      executionModelKey: 'stale/build'
    } })
    await poll

    expect(store.analysisModelKey).toBe('saved/analyze')
    expect(store.executionModelKey).toBe('saved/build')
  })
})
