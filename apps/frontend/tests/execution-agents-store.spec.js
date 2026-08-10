import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), patch: vi.fn(), post: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { absoluteWebsocketUrl, useExecutionAgentStore } from '../src/stores/execution-agents'

const agent = {
  id: 'agent-1',
  name: 'Arbeitsplatz',
  connected: true,
  operations: [
    'workspace.export',
    'workspace.import',
    'workspace.list',
    'workspace.read',
    'workspace.write'
  ],
  roots: [{ id: 'root-1', label: 'Projekte', revision: 'root-revision' }]
}

describe('execution agent store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => vi.useRealTimers())

  it('loads agents and derives the selected workspace context', async () => {
    api.get.mockResolvedValue({ data: { items: [agent] } })
    const store = useExecutionAgentStore()

    await store.fetchAll()

    expect(api.get).toHaveBeenCalledWith('/execution-agents')
    expect(store.workspaceContextFor({
      preferredExecutionAgentId: 'agent-1',
      preferredRootId: 'root-1'
    })).toEqual({
      executionAgentId: 'agent-1',
      rootId: 'root-1',
      connected: true,
      operations: [
        'workspace.export',
        'workspace.import',
        'workspace.list',
        'workspace.read',
        'workspace.write'
      ]
    })
  })

  it('requires the selected root and applies persisted read-only access to live operations', () => {
    const store = useExecutionAgentStore()
    store.items = [agent]

    expect(store.workspaceContextFor({
      preferredExecutionAgentId: 'agent-1',
      preferredRootId: 'missing-root',
      workspaceAccess: 'read-write'
    })).toMatchObject({ connected: false, operations: [] })
    expect(store.workspaceContextFor({
      preferredExecutionAgentId: 'agent-1',
      preferredRootId: 'root-1',
      workspaceAccess: 'read-only',
      workspaceContext: { connected: false, operations: [] }
    })).toMatchObject({
      connected: true,
      operations: ['workspace.export', 'workspace.list', 'workspace.read']
    })
  })

  it('turns relative pairing endpoints into browser-usable websocket URLs', () => {
    expect(absoluteWebsocketUrl(
      '/api/execution-agents/connect',
      { href: 'https://mato.example/settings/execution-agents' }
    )).toBe('wss://mato.example/api/execution-agents/connect')
    expect(absoluteWebsocketUrl(
      'api/execution-agents/connect',
      { href: 'http://localhost:10020/' }
    )).toBe('ws://localhost:10020/api/execution-agents/connect')
  })

  it('shares polling and keeps refreshes non-blocking after the first load', async () => {
    vi.useFakeTimers()
    api.get.mockResolvedValue({ data: { items: [agent] } })
    const store = useExecutionAgentStore()

    store.startPolling()
    store.startPolling()
    await vi.waitFor(() => expect(api.get).toHaveBeenCalledTimes(1))
    expect(store.loading).toBe(false)

    await vi.advanceTimersByTimeAsync(10_000)
    expect(api.get).toHaveBeenCalledTimes(2)
    expect(store.loading).toBe(false)

    store.stopPolling()
    store.stopPolling()
  })

  it('creates, renames and revokes an agent without reloading the list', async () => {
    api.post.mockResolvedValue({
      data: { agent, token: 'once', websocketUrl: 'wss://example.test/agents' }
    })
    api.patch.mockResolvedValue({ data: { ...agent, name: 'Notebook' } })
    api.delete.mockResolvedValue({})
    const store = useExecutionAgentStore()

    const created = await store.create('Arbeitsplatz')
    await store.rename('agent-1', 'Notebook')
    await store.revoke('agent-1')

    expect(created.token).toBe('once')
    expect(api.post).toHaveBeenCalledWith('/execution-agents', { name: 'Arbeitsplatz' })
    expect(api.patch).toHaveBeenCalledWith('/execution-agents/agent-1', { name: 'Notebook' })
    expect(api.delete).toHaveBeenCalledWith('/execution-agents/agent-1')
    expect(store.items).toEqual([])
  })
})
