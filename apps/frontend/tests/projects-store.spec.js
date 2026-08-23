import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), patch: vi.fn(), post: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useProjectStore } from '../src/stores/projects'

const project = {
  id: 'project-1',
  name: 'MatoLoop',
  instructions: 'Use Vue and FastAPI.',
  sessionCount: 2,
  createdAt: '2026-08-18T08:00:00Z',
  updatedAt: '2026-08-18T09:00:00Z'
}

describe('projects store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads projects from the list envelope', async () => {
    api.get.mockResolvedValue({ data: { items: [project] } })
    const store = useProjectStore()

    await store.fetchAll()

    expect(api.get).toHaveBeenCalledWith('/projects')
    expect(store.items).toEqual([project])
    expect(store.loading).toBe(false)
    expect(store.loaded).toBe(true)
    expect(store.loadFailed).toBe(false)
  })

  it('ignores stale reads', async () => {
    let resolveFirst
    api.get
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce({ data: { items: [project] } })
    const store = useProjectStore()
    const first = store.fetchAll()

    await store.fetchAll()
    resolveFirst({ data: { items: [{ ...project, name: 'Veraltet' }] } })
    await first

    expect(store.items).toEqual([project])
    expect(store.loading).toBe(false)
  })

  it('creates, updates, and deletes projects after successful requests', async () => {
    const renamed = { ...project, name: 'Mato Loop', instructions: 'Be concise.', updatedAt: '2026-08-18T10:00:00Z' }
    api.post.mockResolvedValue({ data: project })
    api.patch.mockResolvedValue({ data: renamed })
    api.delete.mockResolvedValue({})
    const store = useProjectStore()

    await store.create(project.name, project.instructions)
    await store.update(project.id, renamed.name, renamed.instructions)
    await store.remove(project.id, { automationImpact: 'impact-token' })

    expect(api.post).toHaveBeenCalledWith('/projects', {
      name: project.name,
      instructions: project.instructions
    })
    expect(api.patch).toHaveBeenCalledWith('/projects/project-1', {
      name: renamed.name,
      instructions: renamed.instructions
    })
    expect(api.delete).toHaveBeenCalledWith('/projects/project-1', {
      params: { automationImpact: 'impact-token' }
    })
    expect(store.items).toEqual([])
  })

  it('loads deletion impact and passes automation parameters when deleting', async () => {
    const impact = {
      sessionCount: 2,
      sessionIds: ['session-1', 'session-2'],
      automationCount: 1,
      requiredAutomationCount: 1,
      automationImpact: 'impact-token'
    }
    api.get.mockResolvedValue({ data: impact })
    api.delete.mockResolvedValue({})
    const store = useProjectStore()

    await expect(store.deleteImpact(project.id, { suppressErrorToast: true })).resolves.toEqual(impact)
    await store.remove(project.id, {
      automationMode: 'deleteAutomations',
      automationImpact: impact.automationImpact
    })

    expect(api.get).toHaveBeenCalledWith('/projects/project-1/delete-impact', {
      suppressErrorToast: true
    })
    expect(api.delete).toHaveBeenCalledWith('/projects/project-1', {
      params: {
        automationMode: 'deleteAutomations',
        automationImpact: 'impact-token'
      }
    })

    await store.remove(project.id, { automationImpact: 'impact-token' })
    expect(api.delete).toHaveBeenLastCalledWith('/projects/project-1', {
      params: { automationImpact: 'impact-token' }
    })
  })

  it('invalidates a pending read only after a mutation succeeds', async () => {
    let resolveLoad
    api.get.mockReturnValue(new Promise((resolve) => { resolveLoad = resolve }))
    api.post.mockResolvedValue({ data: project })
    const store = useProjectStore()
    const load = store.fetchAll()

    await store.create(project.name)
    resolveLoad({ data: { items: [] } })
    await load

    expect(store.items).toEqual([project])
    expect(store.loading).toBe(false)
  })

  it('keeps state unchanged when mutations fail', async () => {
    const error = new Error('request failed')
    api.post.mockRejectedValue(error)
    api.patch.mockRejectedValue(error)
    api.delete.mockRejectedValue(error)
    const store = useProjectStore()
    store.items = [project]

    await expect(store.create('Neu')).rejects.toThrow('request failed')
    await expect(store.update(project.id, 'Neu')).rejects.toThrow('request failed')
    await expect(store.remove(project.id, { automationImpact: 'impact-token' })).rejects.toThrow('request failed')

    expect(store.items).toEqual([project])
    expect(store.revision).toBe(0)
  })

  it('reports a failed current load without discarding existing projects', async () => {
    api.get.mockRejectedValue(new Error('offline'))
    const store = useProjectStore()
    store.items = [project]

    await expect(store.fetchAll()).rejects.toThrow('offline')

    expect(store.items).toEqual([project])
    expect(store.loading).toBe(false)
    expect(store.loaded).toBe(false)
    expect(store.loadFailed).toBe(true)
  })
})
