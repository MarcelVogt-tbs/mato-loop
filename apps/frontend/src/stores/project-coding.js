import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

function initialState() {
  return {
    projectId: null,
    contextVersion: 0,
    models: [],
    modelLoadVersion: 0,
    analysisModelKey: null,
    analysisModelVariant: null,
    executionModelKey: null,
    executionModelVariant: null,
    currentPath: '.',
    fileLoadVersion: 0,
    files: [],
    content: null,
    statuses: [],
    plans: [],
    executions: [],
    executionLoadVersion: 0,
    refreshVersion: 0,
    queuePauseReason: null,
    pending: 0,
    loaded: false,
    loadFailed: false
  }
}

export const useProjectCodingStore = defineStore('project-coding', {
  state: initialState,
  getters: {
    loading: (state) => state.pending > 0
  },
  actions: {
    selectProject(projectId) {
      if (this.projectId === projectId) return
      this.resetProject(projectId)
    },
    resetProject(projectId = this.projectId) {
      const contextVersion = this.contextVersion + 1
      Object.assign(this, initialState(), { projectId, contextVersion })
    },
    async request(callback, { suppressErrorToast = false } = {}) {
      const projectId = this.projectId
      const contextVersion = this.contextVersion
      if (!projectId) return null
      this.pending += 1
      try {
        const result = await callback(projectId, suppressErrorToast ? { suppressErrorToast: true } : undefined)
        return this.projectId === projectId && this.contextVersion === contextVersion ? result : null
      } finally {
        if (this.projectId === projectId && this.contextVersion === contextVersion) {
          this.pending = Math.max(0, this.pending - 1)
        }
      }
    },
    async loadModels(options) {
      const projectId = this.projectId
      const loadVersion = ++this.modelLoadVersion
      const data = await this.request(async (id, config) => (await api.get(`/projects/${id}/coding/models`, config)).data, options)
      if (data && this.projectId === projectId && this.modelLoadVersion === loadVersion) {
        this.models = data.items
        this.analysisModelKey = data.analysisModelKey
        this.analysisModelVariant = data.analysisModelVariant ?? null
        this.executionModelKey = data.executionModelKey
        this.executionModelVariant = data.executionModelVariant ?? null
      }
      return data
    },
    async saveModels(analysisModelKey, analysisModelVariant, executionModelKey, executionModelVariant) {
      const projectId = this.projectId
      const loadVersion = ++this.modelLoadVersion
      const data = await this.request(async (id) => (await api.put(`/projects/${id}/coding/models`, {
        analysisModelKey,
        analysisModelVariant,
        executionModelKey,
        executionModelVariant
      })).data)
      if (data && this.projectId === projectId && this.modelLoadVersion === loadVersion) {
        this.analysisModelKey = data.analysisModelKey
        this.analysisModelVariant = data.analysisModelVariant ?? null
        this.executionModelKey = data.executionModelKey
        this.executionModelVariant = data.executionModelVariant ?? null
      }
      return data
    },
    async loadFiles(path = '.', options) {
      const projectId = this.projectId
      const loadVersion = ++this.fileLoadVersion
      const data = await this.request(async (id, config) => (await api.get(
        `/projects/${id}/coding/files`,
        { ...(config || {}), params: { path } }
      )).data, options)
      if (data && this.projectId === projectId && this.fileLoadVersion === loadVersion) {
        this.currentPath = data.path
        this.files = data.items
        if (!options?.preserveContent) this.content = null
      }
      return data
    },
    closeFile() {
      this.content = null
    },
    async readFile(path) {
      const projectId = this.projectId
      const data = await this.request(async (id, config) => (await api.get(
        `/projects/${id}/coding/files/content`,
        { ...(config || {}), params: { path } }
      )).data)
      if (data && this.projectId === projectId) this.content = data
      return data
    },
    async loadStatuses(options) {
      const projectId = this.projectId
      const data = await this.request(async (id, config) => (await api.get(`/projects/${id}/coding/files/status`, config)).data, options)
      if (data && this.projectId === projectId) this.statuses = data.items
      return data
    },
    async loadPlans(options) {
      const projectId = this.projectId
      const data = await this.request(async (id, config) => (await api.get(`/projects/${id}/coding/plans`, config)).data, options)
      if (data && this.projectId === projectId) this.plans = data.items
      return data
    },
    async enqueue(planIds) {
      const projectId = this.projectId
      const loadVersion = ++this.executionLoadVersion
      const data = await this.request(async (id) => (await api.post(
        `/projects/${id}/coding/plans/enqueue`,
        { planIds }
      )).data)
      if (data && this.projectId === projectId && this.executionLoadVersion === loadVersion) {
        this.executions = [...this.executions, ...data.items]
        this.queuePauseReason = data.queuePauseReason
      }
      return data
    },
    async loadExecutions(options) {
      const projectId = this.projectId
      const loadVersion = ++this.executionLoadVersion
      const data = await this.request(async (id, config) => (await api.get(`/projects/${id}/coding/executions`, config)).data, options)
      if (data && this.projectId === projectId && this.executionLoadVersion === loadVersion) {
        this.executions = data.items
        this.queuePauseReason = data.queuePauseReason
      }
      return data
    },
    async pauseQueue() {
      this.executionLoadVersion += 1
      await this.request((id) => api.post(`/projects/${id}/coding/queue/pause`))
      return this.loadExecutions()
    },
    async resumeQueue() {
      this.executionLoadVersion += 1
      await this.request((id) => api.post(`/projects/${id}/coding/queue/resume`))
      return this.loadExecutions()
    },
    async cancelExecution(executionId) {
      this.executionLoadVersion += 1
      await this.request((id) => api.post(`/projects/${id}/coding/executions/${executionId}/cancel`))
      return this.loadExecutions()
    },
    async replyPermission(executionId, response) {
      this.executionLoadVersion += 1
      await this.request((id) => api.post(
        `/projects/${id}/coding/executions/${executionId}/permission`,
        { response }
      ))
      return this.loadExecutions()
    },
    async refresh({ quiet = false } = {}) {
      const contextVersion = this.contextVersion
      const refreshVersion = ++this.refreshVersion
      const options = quiet
        ? { suppressErrorToast: true, preserveContent: true }
        : { preserveContent: true }
      this.loadFailed = false
      try {
        await Promise.all([
          this.loadModels(options),
          this.loadFiles(this.currentPath, options),
          this.loadStatuses(options),
          this.loadPlans(options),
          this.loadExecutions(options)
        ])
        if (this.contextVersion === contextVersion && this.refreshVersion === refreshVersion) {
          this.loaded = true
        }
      } catch (error) {
        if (this.contextVersion === contextVersion && this.refreshVersion === refreshVersion) {
          this.loadFailed = true
        }
        throw error
      }
    }
  }
})
