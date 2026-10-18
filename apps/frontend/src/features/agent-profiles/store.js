import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useChatStore } from 'src/features/chat/store'

const scopeConfig = (scope) => ({ params: { scope } })

export const useAgentProfileStore = defineStore('agent-profiles', {
  state: () => ({
    agents: [],
    manageableAgents: [],
    baseProfiles: [],
    defaultAgentProfileId: null,
    integrations: [],
    personalAgentsEnabled: true,
    personalToolPermissionsEnabled: false,
    scope: 'personal',
    loaded: false,
    loadFailed: false,
    loading: false,
    loadVersion: 0,
    saving: false
  }),
  getters: {
    byId: (state) => (id) => state.manageableAgents.find((agent) => agent.id === id) || state.agents.find((agent) => agent.id === id) || null
  },
  actions: {
    selectScope(scope) {
      this.scope = scope === 'instance' ? 'instance' : 'personal'
    },
    async fetchAgents(scope = null) {
      const version = ++this.loadVersion
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = scope
          ? await api.get('/agent-profiles', scopeConfig(scope))
          : await api.get('/agent-profiles')
        if (version !== this.loadVersion) return data
        this.agents = Array.isArray(data?.effectiveAgents) ? data.effectiveAgents : []
        this.manageableAgents = Array.isArray(data?.agents) ? data.agents : this.agents
        this.baseProfiles = data?.baseProfiles || this.agents.filter((agent) => agent.protected && agent.scope === 'instance')
        this.defaultAgentProfileId = data?.defaultAgentProfileId || null
        this.integrations = Array.isArray(data?.integrations) ? data.integrations : []
        this.personalAgentsEnabled = data?.personalAgentsEnabled !== false
        this.personalToolPermissionsEnabled = data?.personalToolPermissionsEnabled === true
        this.loaded = true
        return data
      } catch (error) {
        if (version === this.loadVersion) this.loadFailed = true
        throw error
      } finally {
        if (version === this.loadVersion) this.loading = false
      }
    },
    async createAgent(payload) {
      const scope = this.scope
      this.saving = true
      try {
        const { data } = await api.post('/agent-profiles', payload, scopeConfig(scope))
        await this.fetchAgents(scope)
        return this.byId(data?.agentProfileId)
      } finally {
        this.saving = false
      }
    },
    async updateAgent(id, payload) {
      const scope = this.scope
      this.saving = true
      try {
        await api.put(`/agent-profiles/${id}`, payload, scopeConfig(scope))
        await this.fetchAgents(scope)
        return this.byId(id)
      } finally {
        this.saving = false
      }
    },
    async deleteAgent(id) {
      const scope = this.scope
      this.saving = true
      try {
        await api.delete(`/agent-profiles/${id}`, scopeConfig(scope))
        this.agents = this.agents.filter((agent) => agent.id !== id)
        useChatStore().replaceAgentProfile(id, this.defaultAgentProfileId)
      } finally {
        this.saving = false
      }
    },
    async setAgentEnabled(id, enabled, scope) {
      await api.patch(`/agent-profiles/${id}/enabled`, { enabled }, scopeConfig(scope))
    },
    async setDefaultAgent(id) {
      await api.patch('/agent-profiles/default', { profileId: id })
      await this.fetchAgents('instance')
    },
    async setPersonalAgentsEnabled(enabled) {
      const { data } = await api.patch('/agent-profiles/personal-enabled', { enabled })
      this.personalAgentsEnabled = data?.personalAgentsEnabled !== false
      this.defaultAgentProfileId = data?.defaultAgentProfileId || this.defaultAgentProfileId
      if (Array.isArray(data?.effectiveAgents)) this.agents = data.effectiveAgents
    }
  }
})
