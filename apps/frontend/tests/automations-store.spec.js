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

    expect(api.get).toHaveBeenCalledWith('/automations', { params: { archived: false, summary: true } })
    expect(store.items).toEqual([automation])
    expect(store.loading).toBe(false)
  })

  it('loads archived automations separately', async () => {
    api.get.mockResolvedValue({ data: { items: [{ ...automation, archivedAt: '2026-08-20T14:20:00Z' }] } })
    const store = useAutomationStore()

    await store.fetchAutomations({ archived: true })

    expect(api.get).toHaveBeenCalledWith('/automations', { params: { archived: true, summary: true } })
    expect(store.items[0].archivedAt).toBeTruthy()
  })

  it('loads one automation for routed detail and editing pages', async () => {
    api.get.mockResolvedValue({ data: automation })
    const store = useAutomationStore()

    await store.fetchAutomation(automation.id)

    expect(api.get).toHaveBeenCalledWith('/automations/automation-1', { params: { includeHistory: false } })
    expect(store.detail).toEqual(automation)
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
    expect(store.detail).toBeNull()
  })

  it('loads and appends run pages without duplicates', async () => {
    api.get
      .mockResolvedValueOnce({ data: { items: [run], page: 1, hasMore: true } })
      .mockResolvedValueOnce({ data: { items: [run, { ...run, id: 'run-2' }], page: 2, hasMore: false } })
    const store = useAutomationStore()

    await store.fetchRuns(automation.id)
    await store.fetchRuns(automation.id, 2)

    expect(api.get).toHaveBeenNthCalledWith(1, '/automations/automation-1/runs', {
      params: { page: 1, pageSize: 20, summary: true }
    })
    expect(store.runs.map((item) => item.id)).toEqual(['run-1', 'run-2'])
    expect(store.page).toBe(2)
    expect(store.hasMore).toBe(false)
  })

  it('starts a manual run and updates state after the response', async () => {
    let resolveRun
    api.post.mockReturnValue(new Promise((resolve) => { resolveRun = resolve }))
    const store = useAutomationStore()
    store.items = [automation]
    const request = store.runNow(automation.id, automation.revision)

    expect(store.runs).toEqual([])
    expect(store.items[0].effectiveState.activeRunStatus).toBeNull()

    resolveRun({ data: run })
    await request

    expect(api.post).toHaveBeenCalledWith('/automations/automation-1/runs', { expectedRevision: 1 })
    expect(store.runs).toEqual([run])
    expect(store.items[0].effectiveState.activeRunStatus).toBe('queued')
  })

  it('keeps live status separate from the editing definition and invalidates late responses', async () => {
    const store = useAutomationStore()
    store.detail = automation
    api.get.mockResolvedValueOnce({ data: { ...automation, revision: 2 } })
    await store.fetchAutomationStatus(automation.id)
    expect(api.get).toHaveBeenLastCalledWith('/automations/automation-1/status', {})
    expect(store.status.revision).toBe(2)
    expect(store.detail.revision).toBe(1)
    let resolveStatus
    api.get.mockReturnValueOnce(new Promise((resolve) => { resolveStatus = resolve }))
    const request = store.fetchAutomationStatus(automation.id)
    store.clearDetail()
    resolveStatus({ data: automation })
    await request
    expect(store.status).toBeNull()
    expect(store.detail).toBeNull()
  })

  it('updates first-page pagination on refresh while retaining already loaded later pages', async () => {
    const store = useAutomationStore()
    api.get.mockResolvedValueOnce({ data: { items: [run], page: 1, hasMore: false } })
    await store.fetchRuns(automation.id)
    api.get.mockResolvedValueOnce({ data: { items: [run], page: 1, hasMore: true } })
    await store.fetchRuns(automation.id, 1, { preserveLoaded: true })
    expect(store.hasMore).toBe(true)
    api.get.mockResolvedValueOnce({ data: { items: [{ ...run, id: 'older' }], page: 2, hasMore: false } })
    await store.fetchRuns(automation.id, 2)
    api.get.mockResolvedValueOnce({ data: { items: [run], page: 1, hasMore: true } })
    await store.fetchRuns(automation.id, 1, { preserveLoaded: true })
    expect(store.runs.map((item) => item.id)).toEqual(['run-1', 'older'])
    expect(store.page).toBe(2)
    expect(store.hasMore).toBe(false)
  })

  it('loads a run and retries failed delivery', async () => {
    const failed = { ...run, delivery: { status: 'failed', attempts: 1 } }
    const retried = { ...run, delivery: { status: 'pending', attempts: 2 } }
    api.get.mockResolvedValue({ data: failed })
    api.post.mockResolvedValue({ data: retried })
    const store = useAutomationStore()

    await store.fetchRun(automation.id, run.id)
    store.runs = [failed]
    await store.retryDelivery(automation.id, run.id)

    expect(api.get).toHaveBeenCalledWith('/automations/automation-1/runs/run-1')
    expect(api.post).toHaveBeenCalledWith('/automations/automation-1/runs/run-1/delivery-retry')
    expect(store.selectedRun).toEqual(retried)
    expect(store.runs).toEqual([retried])
  })
})
