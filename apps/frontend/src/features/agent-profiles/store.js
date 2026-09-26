import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useChatStore } from 'stores/chat'

const scopeConfig = (scope) => ({ params: { scope } })

export const useAgentProfileStore = defineStore('agent-profiles', {
  state: () => ({
    agents: [],
    manageableAgents: [],
    defaultAgentProfileId: null,
    groups: [],
    groupsByTrust: { private: [], public: [] },
    personas: [],
    integrations: [],
    integrationMetadata: {},
    integrationMetadataLoading: {},
    integrationMetadataFailed: {},
    integrationMetadataGeneration: 0,
    personalAgentsEnabled: true,
    personalPersonasEnabled: true,
    scope: 'personal',
    loaded: false,
    loadFailed: false,
    loading: false,
    saving: false
  }),
  getters: {
    scopedAgents: (state) => state.agents.filter((agent) => (
      agent.scope === state.scope
    )),
    byId: (state) => (id) => state.agents.find((agent) => agent.id === id) || null
  },
  actions: {
    selectScope(scope) {
      const next = scope === 'instance' ? 'instance' : 'personal'
      if (next === this.scope) return
      this.scope = next
      this.integrationMetadata = {}
      this.integrationMetadataLoading = {}
      this.integrationMetadataFailed = {}
      this.integrationMetadataGeneration += 1
    },
    resetIntegrationMetadata() {
      this.integrationMetadata = {}
      this.integrationMetadataLoading = {}
      this.integrationMetadataFailed = {}
      this.integrationMetadataGeneration += 1
    },
    async fetchAgents(scope = null) {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = scope
          ? await api.get('/agent-profiles', scopeConfig(scope))
          : await api.get('/agent-profiles')
        this.agents = Array.isArray(data?.effectiveAgents) ? data.effectiveAgents : []
        this.manageableAgents = Array.isArray(data?.agents) ? data.agents : this.agents
        this.defaultAgentProfileId = data?.defaultAgentProfileId || null
        this.groups = Array.isArray(data?.groups)
          ? data.groups
          : data?.privateGroups || []
        this.groupsByTrust = {
          private: data?.privateGroups || this.groups,
          public: data?.publicGroups || this.groups
        }
        this.integrations = Array.isArray(data?.integrations) ? data.integrations : []
        this.personas = Array.isArray(data?.personas) ? data.personas : []
        this.personalAgentsEnabled = data?.personalAgentsEnabled !== false
        this.personalPersonasEnabled = data?.personalPersonasEnabled !== false
        this.loaded = true
        return data
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    },
    async fetchIntegrationMetadata(id) {
      const scope = this.scope
      const generation = this.integrationMetadataGeneration
      this.integrationMetadataLoading[id] = true
      this.integrationMetadataFailed[id] = false
      try {
        const { data } = await api.get(
          `/agent-profiles/integration-metadata/${id}`,
          scopeConfig(scope)
        )
        if (this.scope !== scope || this.integrationMetadataGeneration !== generation) return null
        this.integrationMetadata[id] = data
        return data
      } catch (error) {
        if (this.scope === scope && this.integrationMetadataGeneration === generation) {
          this.integrationMetadataFailed[id] = true
        }
        throw error
      } finally {
        if (this.scope === scope && this.integrationMetadataGeneration === generation) {
          this.integrationMetadataLoading[id] = false
        }
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
        const accessOnly = this.byId(id)?.accessEditable
        await api.put(`/agent-profiles/${id}${accessOnly ? '/access' : ''}`, accessOnly ? payload.overrides : payload, scopeConfig(scope))
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
    async setPersonalAgentsEnabled(enabled) {
      const { data } = await api.patch('/agent-profiles/personal-enabled', { enabled })
      this.personalAgentsEnabled = data?.personalAgentsEnabled !== false
      this.defaultAgentProfileId = data?.defaultAgentProfileId || this.defaultAgentProfileId
      if (Array.isArray(data?.effectiveAgents)) this.agents = data.effectiveAgents
    }
  }
})
