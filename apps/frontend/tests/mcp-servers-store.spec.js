import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn() }
}))
vi.mock('src/boot/axios', () => ({ api }))

import { useMcpServerStore } from '../src/stores/mcp-servers'

const list = { servers: [{
  id: 'server-1',
  name: 'Dokumentation',
  endpoint: 'https://mcp.example.com/mcp',
  enabled: true,
  tools: [{
    name: 'lookup',
    title: 'Nachschlagen',
    description: 'Vom Server gelieferte Beschreibung',
    inputSchema: { type: 'object' },
    outputSchema: { type: 'object' },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false }
  }]
}] }
const access = {
  integrationId: 'server-1',
  globalPolicy: null,
  effectiveGlobalPolicy: 'auto',
  actions: [{
    capability: 'mcp.call',
    label: 'MCP-Tool aufrufen',
    allowedPolicies: ['disabled', 'ask', 'auto'],
    globalPolicy: 'auto',
    integrationPolicy: 'ask',
    effectiveIntegrationPolicy: 'ask'
  }],
  resources: [{
    key: 'tool:lookup-hash',
    groupKey: null,
    label: 'lookup',
    hardUnavailableCapabilities: [],
    applicableCapabilities: ['mcp.call'],
    policies: { 'mcp.call': 'auto' },
    effectivePolicies: { 'mcp.call': 'auto' }
  }]
}

describe('MCP server store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith('/access') ? access : structuredClone(list) }))
  })

  it('applies successful server writes locally without follow-up list requests', async () => {
    const created = { ...list.servers[0], id: 'server-2', name: 'Suche' }
    const updated = { ...list.servers[0], name: 'Dokumentation intern' }
    const refreshed = { ...updated, toolCount: 2 }
    api.post.mockImplementation((url) => Promise.resolve({ data: url.endsWith('/refresh') ? refreshed : created }))
    api.put.mockResolvedValue({ data: updated })
    api.delete.mockResolvedValue({})
    const store = useMcpServerStore()
    const payload = { name: 'Dokumentation', endpoint: 'https://mcp.example.com/mcp', bearerToken: 'secret', enabled: true }

    await store.fetchServers()
    await store.createServer(payload)
    await store.updateServer('server-1', { ...payload, bearerToken: '' })
    await store.refreshServer('server-1')
    await store.removeServer('server-2')

    expect(api.get).toHaveBeenCalledWith('/mcp/servers', { params: { scope: 'instance' } })
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.post).toHaveBeenCalledWith('/mcp/servers', payload, { params: { scope: 'instance' } })
    expect(api.put).toHaveBeenCalledWith('/mcp/servers/server-1', {
      name: 'Dokumentation', endpoint: 'https://mcp.example.com/mcp', enabled: true
    }, { params: { scope: 'instance' } })
    expect(api.post).toHaveBeenCalledWith('/mcp/servers/server-1/refresh', null, { params: { scope: 'instance' } })
    expect(api.delete).toHaveBeenCalledWith('/mcp/servers/server-2', { params: { scope: 'instance' } })
    expect(store.servers).toEqual([refreshed])
    expect(store.testServer).toBeUndefined()
    expect(store.servers[0].tools[0].annotations.readOnlyHint).toBe(true)
  })

  it('keeps private and public access branches separate', async () => {
    api.put.mockResolvedValue({ data: { ...access, globalPolicy: 'disabled', effectiveGlobalPolicy: 'disabled' } })
    const store = useMcpServerStore()

    await store.fetchAccess('server-1')
    store.selectTrust('public')
    await store.fetchAccess('server-1')
    await store.saveAccess('server-1', {
      globalPolicy: 'disabled',
      policies: { 'mcp.call': 'disabled' },
      resources: { 'tool:lookup-hash': { 'mcp.call': 'auto' } }
    })

    expect(api.get).toHaveBeenNthCalledWith(1, '/mcp/servers/server-1/access', { params: { scope: 'instance', trust: 'private' } })
    expect(api.get).toHaveBeenNthCalledWith(2, '/mcp/servers/server-1/access', { params: { scope: 'instance', trust: 'public' } })
    expect(api.put).toHaveBeenCalledWith('/mcp/servers/server-1/access', {
      globalPolicy: 'disabled',
      policies: { 'mcp.call': 'disabled' },
      resources: { 'tool:lookup-hash': { 'mcp.call': 'auto' } }
    }, { params: { scope: 'instance', trust: 'public' } })
    expect(store.access.globalPolicy).toBe('disabled')
    expect(store.access.effectiveGlobalPolicy).toBe('disabled')
  })

  it('omits the instance global policy from personal access writes', async () => {
    api.put.mockResolvedValue({ data: access })
    const store = useMcpServerStore()
    store.selectScope('personal')

    await store.saveAccess('server-1', { globalPolicy: null, policies: {}, resources: {} })

    expect(api.put).toHaveBeenCalledWith('/mcp/servers/server-1/access', {
      policies: {}, resources: {}
    }, { params: { scope: 'personal', trust: 'private' } })
    expect(store.access.effectiveGlobalPolicy).toBe('auto')
  })

  it('loads and saves the personal-server setting', async () => {
    api.get.mockResolvedValueOnce({ data: { personalAllowed: false } })
    api.put.mockResolvedValueOnce({ data: { personalAllowed: true } })
    const store = useMcpServerStore()

    await store.fetchSettings()
    await store.saveSettings(true)

    expect(api.get).toHaveBeenCalledWith('/mcp/settings')
    expect(api.put).toHaveBeenCalledWith('/mcp/settings', { personalAllowed: true })
    expect(store.personalAllowed).toBe(true)
    expect(store.settingsLoaded).toBe(true)
  })

  it('discards access loaded for a previous trust branch', async () => {
    const requests = []
    api.get.mockImplementation((_url, config) => new Promise((resolve) => requests.push({ config, resolve })))
    const store = useMcpServerStore()

    const privateLoad = store.fetchAccess('server-1')
    store.selectTrust('public')
    const publicLoad = store.fetchAccess('server-1')
    requests[1].resolve({ data: { ...access, branch: 'public' } })
    await publicLoad
    requests[0].resolve({ data: { ...access, branch: 'private' } })
    await privateLoad

    expect(store.access.branch).toBe('public')
    expect(store.accessLoading).toBe(false)
  })
})
