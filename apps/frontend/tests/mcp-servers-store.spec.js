import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn() }
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

describe('MCP server store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    api.get.mockResolvedValue({ data: structuredClone(list) })
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

  it('shows shared and personal servers without changing the mutation scope', async () => {
    const items = [
      { ...list.servers[0], scope: 'instance' },
      { ...list.servers[0], id: 'mine', scope: 'personal' }
    ]
    api.get.mockResolvedValueOnce({ data: { servers: items } })
    const store = useMcpServerStore()
    store.selectScope('personal')

    await store.fetchVisibleServers()

    expect(api.get).toHaveBeenCalledWith('/mcp/servers/visible')
    expect(store.visibleServers).toEqual(items)
    expect(store.scope).toBe('personal')
  })

  it('toggles one owned server without resending its endpoint or token', async () => {
    api.patch.mockResolvedValue({ data: { id: 'mine', enabled: false } })
    const store = useMcpServerStore()

    await store.setServerEnabled('mine', false, 'personal')

    expect(api.patch).toHaveBeenCalledWith('/integrations/mine/enabled', { enabled: false }, { params: { scope: 'personal' } })
  })

})
