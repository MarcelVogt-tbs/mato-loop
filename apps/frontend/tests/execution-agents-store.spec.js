import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { absoluteWebsocketUrl, useExecutionAgentStore } from '../src/stores/execution-agents'

const agent = {
  id: 'agent-1',
  name: 'Arbeitsplatz',
  pairingState: 'paired',
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
    api.get.mockResolvedValue({ data: { items: [agent], defaultExecutionAgentId: 'agent-1' } })
    const store = useExecutionAgentStore()

    await store.fetchAll()

    expect(api.get).toHaveBeenCalledWith('/execution-agents')
    expect(store.defaultExecutionAgentId).toBe('agent-1')
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

  it('requires the selected root and exposes its advertised operations', () => {
    const store = useExecutionAgentStore()
    store.items = [agent]

    expect(store.workspaceContextFor({
      preferredExecutionAgentId: 'agent-1',
      preferredRootId: 'missing-root'
    })).toMatchObject({ connected: false, operations: [] })
    expect(store.workspaceContextFor({
      preferredExecutionAgentId: 'agent-1',
      preferredRootId: 'root-1',
      workspaceContext: { connected: false, operations: [] }
    })).toMatchObject({
      connected: true,
      operations: agent.operations
    })
  })

  it('keeps v2 system operations available without a home directory', () => {
    const store = useExecutionAgentStore()
    store.items = [{
      ...agent,
      protocolVersion: 2,
      roots: [],
      operations: ['system.info', 'filesystem.resolve', 'process.exec']
    }]

    expect(store.workspaceContextFor({ preferredExecutionAgentId: 'agent-1' })).toEqual({
      executionAgentId: 'agent-1',
      rootId: null,
      connected: true,
      operations: ['system.info', 'filesystem.resolve']
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

  it('loads the pairing policy only once', async () => {
    const policy = { defaultDurationSeconds: 1800, maximumDurationSeconds: 86_400 }
    api.get.mockResolvedValue({ data: policy })
    const store = useExecutionAgentStore()

    await store.fetchPairingPolicy()
    await store.fetchPairingPolicy()

    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledWith('/execution-agents/pairing-policy')
    expect(store.pairingPolicy).toEqual(policy)
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

  it('creates and rotates pairing tokens, renames and unpairs without storing secrets', async () => {
    const pendingAgent = { ...agent, connected: false, pairingState: 'pending' }
    const unpairedAgent = { ...pendingAgent, pairingState: 'unpaired' }
    api.post
      .mockResolvedValueOnce({
        data: {
          agent: pendingAgent,
          pairingToken: 'create-once',
          pairingTokenExpiresAt: '2026-08-11T12:30:00Z',
          websocketUrl: 'wss://example.test/agents'
        }
      })
      .mockResolvedValueOnce({
        data: {
          agent: pendingAgent,
          pairingToken: 'rotate-once',
          pairingTokenExpiresAt: '2026-08-11T12:30:00Z',
          websocketUrl: '/api/execution-agents/connect'
        }
      })
    api.patch.mockResolvedValue({ data: { ...agent, name: 'Notebook' } })
    api.delete.mockResolvedValue({ data: unpairedAgent })
    const store = useExecutionAgentStore()
    store.defaultExecutionAgentId = 'agent-1'

    const created = await store.create('Arbeitsplatz', 1800)
    const rotated = await store.createPairingToken('agent-1', 3600)
    await store.rename('agent-1', 'Notebook')
    await store.unpair('agent-1')

    expect(created.pairingToken).toBe('create-once')
    expect(rotated.pairingToken).toBe('rotate-once')
    expect(api.post).toHaveBeenNthCalledWith(1, '/execution-agents', {
      name: 'Arbeitsplatz',
      pairingTokenDurationSeconds: 1800
    })
    expect(api.post).toHaveBeenNthCalledWith(2, '/execution-agents/agent-1/pairing-token', {
      pairingTokenDurationSeconds: 3600
    })
    expect(api.patch).toHaveBeenCalledWith('/execution-agents/agent-1', { name: 'Notebook' })
    expect(api.delete).toHaveBeenCalledWith('/execution-agents/agent-1/pairing')
    expect(store.items).toEqual([unpairedAgent])
    expect(store.items[0]).not.toHaveProperty('pairingToken')
    expect(store.defaultExecutionAgentId).toBeNull()
  })

  it('revokes an agent and removes it from the list', async () => {
    api.delete.mockResolvedValue({})
    const store = useExecutionAgentStore()
    store.items = [agent]
    store.defaultExecutionAgentId = 'agent-1'

    await store.revoke('agent-1')

    expect(api.delete).toHaveBeenCalledWith('/execution-agents/agent-1')
    expect(store.items).toEqual([])
    expect(store.defaultExecutionAgentId).toBeNull()
  })

  it('sets, replaces and clears the optional default computer', async () => {
    api.put
      .mockResolvedValueOnce({ data: { defaultExecutionAgentId: 'agent-1' } })
      .mockResolvedValueOnce({ data: { defaultExecutionAgentId: 'agent-2' } })
      .mockResolvedValueOnce({ data: { defaultExecutionAgentId: null } })
    const store = useExecutionAgentStore()

    await store.setDefault('agent-1')
    await store.setDefault('agent-2')
    await store.setDefault(null)

    expect(api.put).toHaveBeenNthCalledWith(1, '/execution-agents/default', {
      defaultExecutionAgentId: 'agent-1'
    })
    expect(api.put).toHaveBeenNthCalledWith(2, '/execution-agents/default', {
      defaultExecutionAgentId: 'agent-2'
    })
    expect(api.put).toHaveBeenNthCalledWith(3, '/execution-agents/default', {
      defaultExecutionAgentId: null
    })
    expect(store.defaultExecutionAgentId).toBeNull()
  })

  it('sets a validated computer home directory', async () => {
    api.put.mockResolvedValue({ data: { ...agent, homeDirectory: '/workspace' } })
    const store = useExecutionAgentStore()
    store.items = [agent]

    await store.setHome('agent-1', '/workspace')

    expect(api.put).toHaveBeenCalledWith('/execution-agents/agent-1/home', {
      homeDirectory: '/workspace'
    })
    expect(store.items[0].homeDirectory).toBe('/workspace')
  })

  it('removes a computer home directory', async () => {
    api.put.mockResolvedValue({ data: { ...agent, homeDirectory: null } })
    const store = useExecutionAgentStore()
    store.items = [{ ...agent, homeDirectory: '/workspace' }]

    await store.setHome('agent-1', null)

    expect(api.put).toHaveBeenCalledWith('/execution-agents/agent-1/home', {
      homeDirectory: null
    })
    expect(store.items[0].homeDirectory).toBeNull()
  })
})
