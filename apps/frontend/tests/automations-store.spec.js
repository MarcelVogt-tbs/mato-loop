import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useAutomationStore } from '../src/stores/automations'

const automation = {
  id: 'automation-1', name: 'Bericht', revision: 1,
  effectiveState: { enabled: true, activeRunStatus: null }
}
const run = {
  id: 'run-1', automationId: automation.id, status: 'queued', delivery: { status: 'pending' }
}

describe('automation store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads the list and ignores stale responses', async () => {
    let resolveFirst
    api.get
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce({ data: { items: [automation] } })
    const store = useAutomationStore()
    const first = store.fetchAutomations()

    await store.fetchAutomations()
    resolveFirst({ data: { items: [{ id: 'old' }] } })
    await first

    expect(api.get).toHaveBeenCalledWith('/automations', { params: { summary: true } })
    expect(store.items).toEqual([automation])
    expect(store.loading).toBe(false)
  })

  it('keeps disabled automations in the normal list', async () => {
    api.get.mockResolvedValue({ data: { items: [{ ...automation, enabled: false }] } })
    const store = useAutomationStore()

    await store.fetchAutomations()

    expect(store.items[0].enabled).toBe(false)
  })

  it('loads one automation for routed detail and editing pages', async () => {
    api.get.mockResolvedValue({ data: automation })
    const store = useAutomationStore()

    const result = await store.fetchAutomation(automation.id)

    expect(api.get).toHaveBeenCalledWith('/automations/automation-1', { params: { includeHistory: false } })
    expect(result).toEqual(automation)
  })

  it('creates, updates and deletes only after successful requests', async () => {
    const payload = { name: 'Bericht', task: 'Erstellen', schedules: [] }
    const updated = { ...automation, revision: 2, name: 'Neuer Bericht' }
    api.post.mockResolvedValue({ data: automation })
    api.put.mockResolvedValue({ data: updated })
    api.delete.mockResolvedValue({})
    const store = useAutomationStore()

    await store.createAutomation(payload)
    await store.updateAutomation(automation.id, { ...payload, expectedRevision: 1 })
    await store.deleteAutomation(automation.id, 2)

    expect(api.post).toHaveBeenCalledWith('/automations', payload)
    expect(api.put).toHaveBeenCalledWith('/automations/automation-1', { ...payload, expectedRevision: 1 })
    expect(api.delete).toHaveBeenCalledWith('/automations/automation-1', { params: { expectedRevision: 2 } })
    expect(store.items).toEqual([])
  })

  it('keeps state unchanged when a mutation fails', async () => {
    api.put.mockRejectedValue(new Error('request failed'))
    const store = useAutomationStore()
    store.items = [automation]

    await expect(store.updateAutomation(automation.id, { expectedRevision: 1 })).rejects.toThrow('request failed')

    expect(store.items).toEqual([automation])
  })

  it('starts a manual run and updates state after the response', async () => {
    let resolveRun
    api.post.mockReturnValue(new Promise((resolve) => { resolveRun = resolve }))
    const store = useAutomationStore()
    store.items = [automation]
    const request = store.runNow(automation.id, automation.revision)

    expect(store.items[0].effectiveState.activeRunStatus).toBeNull()

    resolveRun({ data: run })
    await request

    expect(api.post).toHaveBeenCalledWith('/automations/automation-1/runs', { expectedRevision: 1 })
    expect(store.items[0].effectiveState.activeRunStatus).toBe('queued')
  })

  it('loads a run for its session history', async () => {
    api.get.mockResolvedValue({ data: run })
    const store = useAutomationStore()

    const result = await store.fetchRun(automation.id, run.id)

    expect(api.get).toHaveBeenCalledWith('/automations/automation-1/runs/run-1')
    expect(result).toEqual(run)
  })
})
