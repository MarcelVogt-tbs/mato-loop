import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const READ_ONLY_OPERATIONS = new Set([
  'workspace.export',
  'workspace.list',
  'workspace.read',
  'workspace.glob',
  'workspace.grep',
  'workspace.stat'
])
const POLL_INTERVAL_MS = 10_000
let fetchPromise = null
let pairingPolicyPromise = null
let pollTimer = null
let pollConsumers = 0

export function absoluteWebsocketUrl(value, location = globalThis.location) {
  if (typeof value !== 'string' || !value.trim()) return value
  try {
    const url = new URL(value, location?.href)
    if (url.protocol === 'http:') url.protocol = 'ws:'
    else if (url.protocol === 'https:') url.protocol = 'wss:'
    return url.toString()
  } catch {
    return value
  }
}

export const useExecutionAgentStore = defineStore('execution-agents', {
  state: () => ({
    items: [],
    defaultExecutionAgentId: null,
    pairingPolicy: null,
    loading: false,
    loaded: false,
    loadFailed: false
  }),
  getters: {
    byId: (state) => (id) => state.items.find((agent) => agent.id === id) || null,
    workspaceContextFor: (state) => (preferences) => {
      const executionAgentId = preferences?.preferredExecutionAgentId || null
      const agent = state.items.find((item) => item.id === executionAgentId)
      const rootId = preferences?.preferredRootId || null
      const root = agent?.roots?.find((item) => item.id === rootId)
      const availableOperations = root
        ? agent?.operations || []
        : (agent?.operations || []).filter((operation) => (
            operation === 'system.info' || operation === 'filesystem.resolve'
          ))
      const connected = Boolean(agent?.connected && (root || agent?.protocolVersion >= 2))
      const operations = connected
        ? availableOperations.filter((operation) => (
            preferences?.workspaceAccess !== 'read-only'
            || !operation.startsWith('workspace.')
            || READ_ONLY_OPERATIONS.has(operation)
          ))
        : []
      return {
        executionAgentId,
        rootId,
        connected,
        operations
      }
    }
  },
  actions: {
    async fetchAll(config = null) {
      if (fetchPromise) return fetchPromise
      const initialLoad = !this.loaded
      if (initialLoad) this.loading = true
      this.loadFailed = false
      fetchPromise = config
        ? api.get('/execution-agents', config)
        : api.get('/execution-agents')
      try {
        const { data } = await fetchPromise
        this.items = data.items
        this.defaultExecutionAgentId = data.defaultExecutionAgentId || null
        this.loaded = true
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        if (initialLoad) this.loading = false
        fetchPromise = null
      }
    },
    async fetchPairingPolicy() {
      if (this.pairingPolicy) return this.pairingPolicy
      if (!pairingPolicyPromise) pairingPolicyPromise = api.get('/execution-agents/pairing-policy')
      try {
        const { data } = await pairingPolicyPromise
        this.pairingPolicy = data
        return data
      } finally {
        pairingPolicyPromise = null
      }
    },
    startPolling() {
      pollConsumers += 1
      if (pollTimer) return
      const refresh = () => {
        if (typeof document === 'undefined' || document.visibilityState === 'visible') {
          void this.fetchAll(this.loaded ? { suppressErrorToast: true } : null).catch(() => {})
        }
      }
      refresh()
      pollTimer = globalThis.setInterval(refresh, POLL_INTERVAL_MS)
    },
    stopPolling() {
      pollConsumers = Math.max(0, pollConsumers - 1)
      if (pollConsumers || !pollTimer) return
      globalThis.clearInterval(pollTimer)
      pollTimer = null
    },
    async create(name, pairingTokenDurationSeconds) {
      const { data } = await api.post('/execution-agents', { name, pairingTokenDurationSeconds })
      this.items = [...this.items.filter((item) => item.id !== data.agent.id), data.agent]
      this.loaded = true
      return { ...data, websocketUrl: absoluteWebsocketUrl(data.websocketUrl) }
    },
    async rename(id, name) {
      const { data } = await api.patch(`/execution-agents/${id}`, { name })
      const updated = data.agent || data
      this.items = this.items.map((agent) => agent.id === id ? { ...agent, ...updated } : agent)
      return updated
    },
    async setDefault(defaultExecutionAgentId) {
      const { data } = await api.put('/execution-agents/default', { defaultExecutionAgentId })
      this.defaultExecutionAgentId = data.defaultExecutionAgentId || null
      return this.defaultExecutionAgentId
    },
    async setHome(id, homeDirectory) {
      const { data } = await api.put(`/execution-agents/${id}/home`, { homeDirectory })
      this.items = this.items.map((agent) => agent.id === id ? { ...agent, ...data } : agent)
      return data
    },
    async createPairingToken(id, pairingTokenDurationSeconds) {
      const { data } = await api.post(`/execution-agents/${id}/pairing-token`, { pairingTokenDurationSeconds })
      this.items = this.items.map((agent) => agent.id === id ? data.agent : agent)
      return { ...data, websocketUrl: absoluteWebsocketUrl(data.websocketUrl) }
    },
    async unpair(id) {
      const { data } = await api.delete(`/execution-agents/${id}/pairing`)
      const updated = data.agent || data
      this.items = this.items.map((agent) => agent.id === id ? updated : agent)
      if (this.defaultExecutionAgentId === id) this.defaultExecutionAgentId = null
      return updated
    },
    async revoke(id) {
      await api.delete(`/execution-agents/${id}`)
      this.items = this.items.filter((agent) => agent.id !== id)
      if (this.defaultExecutionAgentId === id) this.defaultExecutionAgentId = null
    }
  }
})
