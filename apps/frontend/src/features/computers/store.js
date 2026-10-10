import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const POLL_INTERVAL_MS = 10_000
let fetchPromise = null
let pairingPolicyPromise = null
let pollTimer = null
let pollConsumers = 0
let pollRefresh = null

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
    backendVersion: null,
    installationTarget: null,
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
      const connected = Boolean(agent?.connected)
      return {
        executionAgentId,
        connected,
        operations: connected ? agent.operations || [] : []
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
        this.backendVersion = data.backendVersion || null
        this.installationTarget = data.installationTarget || null
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
      pollRefresh = refresh
      globalThis.document?.addEventListener('visibilitychange', refresh)
      pollTimer = globalThis.setInterval(refresh, POLL_INTERVAL_MS)
    },
    stopPolling() {
      pollConsumers = Math.max(0, pollConsumers - 1)
      if (pollConsumers || !pollTimer) return
      globalThis.clearInterval(pollTimer)
      globalThis.document?.removeEventListener('visibilitychange', pollRefresh)
      pollRefresh = null
      pollTimer = null
    },
    async create(settings, pairingTokenDurationSeconds) {
      const { data } = await api.post('/execution-agents', { ...settings, pairingTokenDurationSeconds })
      this.items = [...this.items.filter((item) => item.id !== data.agent.id), data.agent]
      this.loaded = true
      return { ...data, websocketUrl: absoluteWebsocketUrl(data.websocketUrl) }
    },
    async update(id, settings) {
      const { data } = await api.patch(`/execution-agents/${id}`, settings)
      const updated = data.agent || data
      this.items = this.items.map((agent) => agent.id === id ? { ...agent, ...updated } : agent)
      return updated
    },
    async setDefault(defaultExecutionAgentId) {
      const { data } = await api.put('/execution-agents/default', { defaultExecutionAgentId })
      this.defaultExecutionAgentId = data.defaultExecutionAgentId || null
      return this.defaultExecutionAgentId
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
    async startMaintenance(id, action, sudoPassword = null, config = {}) {
      const { data } = await api.post(`/execution-agents/${id}/maintenance`, {
        action,
        ...(sudoPassword ? { sudoPassword } : {})
      }, config)
      this.items = this.items.map((agent) => agent.id === id
        ? { ...agent, maintenance: data.maintenance }
        : agent)
    },
    async revoke(id) {
      await api.delete(`/execution-agents/${id}`)
      this.items = this.items.filter((agent) => agent.id !== id)
      if (this.defaultExecutionAgentId === id) this.defaultExecutionAgentId = null
    }
  }
})
