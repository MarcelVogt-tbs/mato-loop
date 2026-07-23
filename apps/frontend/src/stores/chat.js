import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { api } from 'src/boot/axios'
import { useApprovalStore } from 'src/stores/approvals'
import { notifyError } from 'src/utils/notifications'

const DRAFT_STORAGE_KEY = 'matoLoopChatDrafts'

function defaultSessionOptions() {
  return {
    modelId: null,
    memoryMode: 'full',
    effectiveMemoryMode: 'full',
    memoryToolsAvailable: true,
    memoryUnavailableReason: null,
    trustLevel: 'public',
    webSearchPolicy: 'available',
    workingDirectory: null,
    workspaceAccess: 'read-only'
  }
}

function emptyDrafts() {
  return {
    newSession: { message: '', options: defaultSessionOptions() },
    sessionMessages: {}
  }
}

function emptyActivity(revision = 0) {
  return {
    phase: 'idle',
    startedAt: null,
    tool: null,
    modelRequest: null,
    revision
  }
}

function sortSessions(sessions) {
  return [...sessions].sort((left, right) => {
    if (Boolean(left.pinned) !== Boolean(right.pinned)) return left.pinned ? -1 : 1
    const leftUpdatedAt = Date.parse(left.updatedAt || '') || 0
    const rightUpdatedAt = Date.parse(right.updatedAt || '') || 0
    return rightUpdatedAt - leftUpdatedAt
  })
}

function loadDrafts() {
  if (typeof window === 'undefined') return emptyDrafts()
  try {
    const stored = JSON.parse(window.localStorage.getItem(DRAFT_STORAGE_KEY) || '{}')
    const sessionMessages = Object.fromEntries(
      Object.entries(stored.sessionMessages || {}).filter(([, value]) => typeof value === 'string')
    )
    const options = stored.newSession?.options || {}
    return {
      newSession: {
        message: typeof stored.newSession?.message === 'string' ? stored.newSession.message : '',
        options: {
          modelId: typeof options.modelId === 'string' ? options.modelId : null,
          memoryMode: ['full', 'read_only', 'disabled'].includes(options.memoryMode)
            ? options.memoryMode
            : 'full',
          effectiveMemoryMode: ['full', 'read_only', 'disabled'].includes(options.effectiveMemoryMode)
            ? options.effectiveMemoryMode
            : 'full',
          memoryToolsAvailable: options.memoryToolsAvailable !== false,
          memoryUnavailableReason: typeof options.memoryUnavailableReason === 'string'
            ? options.memoryUnavailableReason
            : null,
          trustLevel: options.trustLevel === 'private' ? 'private' : 'public',
          webSearchPolicy: ['required', 'available', 'disabled'].includes(options.webSearchPolicy)
            ? options.webSearchPolicy
            : (options.forceWebSearch === true ? 'required' : 'available'),
          workingDirectory: typeof options.workingDirectory === 'string' ? options.workingDirectory : null,
          workspaceAccess: options.workspaceAccess === 'read-write' ? 'read-write' : 'read-only'
        }
      },
      sessionMessages
    }
  } catch {
    return emptyDrafts()
  }
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    sessions: [],
    current: null,
    loading: false,
    running: false,
    stopping: false,
    cancelRequested: false,
    streamContent: '',
    streamReasoning: null,
    streamModelName: null,
    eventSource: null,
    selectedSessionId: null,
    runSessionId: null,
    runId: null,
    reconnectTimer: null,
    reconnectAttempts: 0,
    activity: emptyActivity(),
    sessionLoadVersion: 0,
    sessionDefaults: defaultSessionOptions(),
    drafts: loadDrafts()
  }),
  getters: {
    activeContext: (state) => state.current || state.drafts.newSession.options,
    activeDraft: (state) => state.current
      ? state.drafts.sessionMessages[state.current.id] || ''
      : state.drafts.newSession.message
  },
  actions: {
    setActivity(phase, details = {}) {
      const startedAt = details.startedAt || this.activity.startedAt || new Date().toISOString()
      this.activity = {
        phase,
        startedAt,
        tool: details.tool || null,
        modelRequest: details.modelRequest || null,
        revision: this.activity.revision + 1
      }
    },
    clearActivity() {
      this.activity = emptyActivity(this.activity.revision + 1)
    },
    persistDrafts() {
      if (typeof window === 'undefined') return
      try {
        window.localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(this.drafts))
      } catch {
        // Der Chat bleibt auch dann bedienbar, wenn lokaler Speicher blockiert oder voll ist.
      }
    },
    async fetchDefaults() {
      this.sessionDefaults = (await api.get('/sessions/defaults')).data
      const options = this.drafts.newSession.options
      if (options.modelId === null) {
        Object.assign(options, this.sessionDefaults)
      } else {
        const { data } = await api.get('/sessions/defaults', {
          params: {
            modelId: options.modelId,
            memoryMode: options.memoryMode || 'full'
          }
        })
        options.effectiveMemoryMode = data.effectiveMemoryMode
        options.memoryToolsAvailable = data.memoryToolsAvailable
        options.memoryUnavailableReason = data.memoryUnavailableReason
        options.trustLevel = data.trustLevel === 'private' ? 'private' : 'public'
      }
      this.persistDrafts()
    },
    reconcileNewSession(validModelIds, searchAvailable) {
      const options = this.drafts.newSession.options
      if (!validModelIds.includes(options.modelId)) {
        options.modelId = validModelIds.includes(this.sessionDefaults.modelId)
          ? this.sessionDefaults.modelId
          : null
        if (options.modelId) {
          const memoryDenied = this.sessionDefaults.effectiveMemoryMode === 'disabled'
          if (options.memoryMode === 'disabled') {
            options.effectiveMemoryMode = 'disabled'
            options.memoryToolsAvailable = false
            options.memoryUnavailableReason = 'memory.sessionDisabled'
          } else {
            options.effectiveMemoryMode = memoryDenied
              ? 'disabled'
              : options.memoryMode
            options.memoryToolsAvailable = this.sessionDefaults.memoryToolsAvailable
            options.memoryUnavailableReason = this.sessionDefaults.memoryUnavailableReason
          }
          options.trustLevel = this.sessionDefaults.trustLevel
        } else {
          options.effectiveMemoryMode = 'disabled'
          options.memoryToolsAvailable = false
          options.memoryUnavailableReason = 'session.modelUnavailable'
          options.trustLevel = 'public'
        }
      }
      if (!searchAvailable && options.webSearchPolicy === 'required') {
        options.webSearchPolicy = 'available'
      }
      this.persistDrafts()
    },
    setDraft(message) {
      if (this.current) this.drafts.sessionMessages[this.current.id] = message
      else this.drafts.newSession.message = message
      this.persistDrafts()
    },
    resetNewDraft() {
      this.drafts.newSession = {
        message: '',
        options: { ...this.sessionDefaults }
      }
      this.persistDrafts()
    },
    async fetchSessions() {
      this.sessions = (await api.get('/sessions')).data
      const sessionIds = new Set(this.sessions.map((session) => session.id))
      for (const id of Object.keys(this.drafts.sessionMessages)) {
        if (!sessionIds.has(id)) delete this.drafts.sessionMessages[id]
      }
      this.persistDrafts()
    },
    async refreshGeneratedTitle(sessionId, expectedTitle) {
      if (!expectedTitle) return
      for (const delay of [1000, 2000, 4000, 8000, 15000, 30000]) {
        await new Promise((resolve) => setTimeout(resolve, delay))
        const localTitle = this.current?.id === sessionId
          ? this.current.title
          : this.sessions.find((item) => item.id === sessionId)?.title
        if (localTitle && localTitle !== expectedTitle) return
        try {
          await this.fetchSessions()
        } catch {
          continue
        }
        const session = this.sessions.find((item) => item.id === sessionId)
        if (!session || session.title === expectedTitle) continue
        if (this.current?.id === sessionId) this.current.title = session.title
        return
      }
    },
    async loadSession(id) {
      this.selectedSessionId = id
      this.sessionLoadVersion += 1
      const loadVersion = this.sessionLoadVersion
      if (this.runSessionId !== id) {
        clearTimeout(this.reconnectTimer)
        this.reconnectTimer = null
        this.reconnectAttempts = 0
        this.eventSource?.close()
        this.eventSource = null
        this.runSessionId = null
        this.runId = null
        this.running = false
        this.stopping = false
        this.cancelRequested = false
        this.streamContent = ''
        this.streamReasoning = null
        this.streamModelName = null
        this.clearActivity()
      }
      if (!id) {
        this.current = null
        this.loading = false
        return
      }
      this.loading = true
      try {
        const session = (await api.get(`/sessions/${id}`)).data
        if (this.selectedSessionId !== id || this.sessionLoadVersion !== loadVersion) return
        this.current = session
        if (session.activeRun && this.runId !== session.activeRun.id) {
          this.watchRun(id, session.activeRun.id, session.activeRun)
        } else if (!session.activeRun && !this.running) {
          this.clearActivity()
        }
      } finally {
        if (this.sessionLoadVersion === loadVersion) this.loading = false
      }
    },
    async refreshSession(id) {
      if (this.selectedSessionId !== id) return
      this.sessionLoadVersion += 1
      const loadVersion = this.sessionLoadVersion
      const session = (await api.get(`/sessions/${id}`)).data
      if (this.selectedSessionId === id && this.sessionLoadVersion === loadVersion) {
        this.current = session
      }
    },
    async updateActive(patch) {
      if (this.current) {
        await this.updateCurrent(patch)
        return
      }
      const options = this.drafts.newSession.options
      const fields = {
        model_id: 'modelId',
        memory_mode: 'memoryMode',
        web_search_policy: 'webSearchPolicy',
        working_directory: 'workingDirectory',
        workspace_access: 'workspaceAccess'
      }
      for (const [field, value] of Object.entries(patch)) {
        if (fields[field]) options[fields[field]] = value
        if (field === 'memory_mode') {
          options.effectiveMemoryMode = options.memoryUnavailableReason
            && options.memoryUnavailableReason !== 'memory.modelToolsUnavailable'
            ? 'disabled'
            : value
        }
      }
      if (Object.hasOwn(patch, 'model_id')) {
        const selectedModelId = patch.model_id
        const { data } = await api.get('/sessions/defaults', {
          params: {
            modelId: selectedModelId,
            memoryMode: options.memoryMode || 'full'
          }
        })
        if (options.modelId === selectedModelId) {
          options.effectiveMemoryMode = data.effectiveMemoryMode
          options.memoryToolsAvailable = data.memoryToolsAvailable
          options.memoryUnavailableReason = data.memoryUnavailableReason
          options.trustLevel = data.trustLevel
        }
      }
      this.persistDrafts()
    },
    async updateCurrent(patch) {
      if (!this.current) return
      const { data } = await api.patch(`/sessions/${this.current.id}`, patch)
      this.current = { ...this.current, ...data }
      const index = this.sessions.findIndex((item) => item.id === data.id)
      if (index >= 0) this.sessions[index] = { ...this.sessions[index], ...data }
    },
    async renameSession(id, title) {
      const { data } = await api.patch(`/sessions/${id}`, { title })
      this.sessions = sortSessions(
        this.sessions.map((session) => session.id === id ? { ...session, ...data } : session)
      )
      if (this.current?.id === id) this.current = { ...this.current, ...data }
    },
    async setSessionPinned(id, pinned) {
      const { data } = await api.patch(`/sessions/${id}`, { pinned })
      this.sessions = sortSessions(
        this.sessions.map((session) => session.id === id ? { ...session, ...data } : session)
      )
      if (this.current?.id === id) this.current = { ...this.current, ...data }
    },
    async deleteSession(id) {
      await api.delete(`/sessions/${id}`)
      this.sessions = this.sessions.filter((item) => item.id !== id)
      delete this.drafts.sessionMessages[id]
      this.persistDrafts()
      if (this.current?.id === id) this.current = null
    },
    async send(content) {
      if (this.running) return null
      return this.current ? this.sendToCurrent(content) : this.startSession(content)
    },
    async stop() {
      if (!this.running || this.stopping) return
      this.cancelRequested = true
      this.stopping = true
      await this.cancelActiveRun()
    },
    async cancelActiveRun() {
      if (!this.cancelRequested || !this.runSessionId || !this.runId) return
      const sessionId = this.runSessionId
      const runId = this.runId
      try {
        await api.post(`/sessions/${sessionId}/runs/${runId}/cancel`)
      } catch {
        if (this.runSessionId === sessionId && this.runId === runId) {
          this.cancelRequested = false
          this.stopping = false
        }
      }
    },
    async startSession(content) {
      this.running = true
      this.streamContent = ''
      this.streamReasoning = null
      this.streamModelName = null
      this.setActivity('submitting', { startedAt: new Date().toISOString() })
      const options = this.drafts.newSession.options
      try {
        const { data } = await api.post('/sessions', {
          content,
          model_id: options.modelId,
          memory_mode: options.memoryMode,
          web_search_policy: options.webSearchPolicy,
          working_directory: options.workingDirectory,
          workspace_access: options.workspaceAccess
        })
        const localMessage = { id: `local-${Date.now()}`, role: 'user', content }
        const session = { ...data.session, messages: [localMessage] }
        this.current = session
        this.selectedSessionId = session.id
        this.sessions.unshift(data.session)
        this.resetNewDraft()
        this.watchRun(session.id, data.runId, { status: 'queued', modelName: data.modelName })
        await this.cancelActiveRun()
        return { created: true, sessionId: session.id }
      } catch (error) {
        this.running = false
        this.stopping = false
        this.cancelRequested = false
        this.clearActivity()
        throw error
      }
    },
    async sendToCurrent(content) {
      const session = this.current
      this.running = true
      this.streamContent = ''
      this.streamReasoning = null
      this.streamModelName = null
      this.setActivity('submitting', { startedAt: new Date().toISOString() })
      const localMessage = { id: `local-${Date.now()}`, role: 'user', content }
      session.messages.push(localMessage)
      try {
        const { data } = await api.post(`/sessions/${session.id}/messages`, { content })
        delete this.drafts.sessionMessages[session.id]
        this.persistDrafts()
        this.watchRun(session.id, data.runId, { status: 'queued', modelName: data.modelName })
        await this.cancelActiveRun()
        return { created: false, sessionId: session.id }
      } catch (error) {
        session.messages = session.messages.filter((message) => message.id !== localMessage.id)
        this.running = false
        this.stopping = false
        this.cancelRequested = false
        this.clearActivity()
        throw error
      }
    },
    watchRun(sessionId, runId, run = {}) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
      if (!run.reconnecting) this.reconnectAttempts = 0
      this.eventSource?.close()
      const source = new EventSource(`/api/sessions/${sessionId}/runs/${runId}/events`)
      let finished = false
      this.eventSource = markRaw(source)
      this.runSessionId = sessionId
      this.runId = runId
      this.running = true
      this.streamModelName = run.modelName || this.streamModelName
      source.onopen = () => {
        if (this.eventSource !== source) return
        this.reconnectAttempts = 0
        if (this.activity.phase === 'reconnecting') {
          const connectedPhase = run.status === 'waiting_approval'
            ? 'waiting_approval'
            : run.status === 'queued' ? 'queued' : 'preparing_context'
          this.setActivity(connectedPhase, { startedAt: run.createdAt })
        }
      }
      if (run.reconnecting) {
        this.setActivity('reconnecting', { startedAt: run.createdAt })
      } else {
        const resumedPhase = run.status === 'waiting_approval'
          ? 'waiting_approval'
          : run.status === 'queued' ? 'queued' : 'preparing_context'
        this.setActivity(resumedPhase, { startedAt: run.createdAt })
      }
      source.addEventListener('run.started', (event) => {
        if (this.eventSource !== source) return
        const payload = JSON.parse(event.data)
        this.streamModelName = payload.modelName || this.streamModelName
        this.setActivity('preparing_context', { startedAt: payload.createdAt })
      })
      source.addEventListener('run.phase', (event) => {
        if (this.eventSource !== source) return
        const payload = JSON.parse(event.data)
        this.setActivity(payload.phase, payload)
      })
      source.addEventListener('session.title.updated', (event) => {
        if (this.eventSource !== source) return
        const payload = JSON.parse(event.data)
        if (this.current?.id === payload.sessionId) this.current.title = payload.title
        const index = this.sessions.findIndex((session) => session.id === payload.sessionId)
        if (index >= 0) this.sessions[index] = { ...this.sessions[index], title: payload.title }
      })
      source.addEventListener('message.delta', (event) => {
        if (this.eventSource !== source) return
        if (this.activity.phase !== 'streaming') this.setActivity('streaming')
        this.streamContent += JSON.parse(event.data).delta
      })
      source.addEventListener('reasoning.updated', (event) => {
        if (this.eventSource !== source) return
        this.streamReasoning = JSON.parse(event.data).reasoning
      })
      const promoteStreamedMessage = (message) => {
        if (message && this.current?.id === sessionId) {
          if (!this.current.messages) this.current.messages = []
          const messages = this.current.messages
          const interrupted = message.generationStatus === 'interrupted'
          this.streamModelName = message.modelName || this.streamModelName
          const promoted = {
            ...message,
            content: interrupted ? message.content : this.streamContent || message.content,
            reasoning: interrupted ? message.reasoning : this.streamReasoning || message.reasoning,
            modelName: this.streamModelName
          }
          const index = messages.findIndex((item) => item.id === message.id)
          if (index === -1) messages.push(promoted)
          else messages[index] = { ...messages[index], ...promoted }
        }
        this.streamContent = ''
        this.streamReasoning = null
      }
      source.addEventListener('message.completed', (event) => {
        if (this.eventSource !== source) return
        const payload = JSON.parse(event.data)
        promoteStreamedMessage(payload.message)
        this.setActivity(payload.message?.toolCalls?.length ? 'preparing_tools' : 'finalizing')
        this.refreshSession(sessionId)
      })
      source.addEventListener('message.interrupted', (event) => {
        if (this.eventSource !== source) return
        promoteStreamedMessage(JSON.parse(event.data).message)
      })
      source.addEventListener('tool.completed', () => {
        if (this.eventSource !== source) return
        this.refreshSession(sessionId)
      })
      source.addEventListener('approval.created', (event) => {
        if (this.eventSource !== source) return
        const approval = JSON.parse(event.data).approval
        this.setActivity('waiting_approval', { tool: approval.details?.tool })
        useApprovalStore().add(approval)
      })
      source.addEventListener('approval.resolved', () => {
        if (this.eventSource !== source) return
        this.setActivity('processing_tool', { tool: this.activity.tool })
        useApprovalStore().fetchPending()
      })
      source.addEventListener('approval.expired', () => {
        if (this.eventSource !== source) return
        this.setActivity('processing_tool', { tool: this.activity.tool })
        useApprovalStore().fetchPending()
      })
      const finish = () => {
        if (this.eventSource !== source) return
        const current = this.current?.id === sessionId ? this.current : null
        const userMessages = current?.messages?.filter((message) => message.role === 'user') || []
        const firstUserTitle = userMessages[0]?.content
          ?.trim()
          .replace(/\n/g, ' ')
          .slice(0, 80)
        const expectedTitle = userMessages.length === 1 && current?.title === firstUserTitle
          ? current.title
          : null
        finished = true
        clearTimeout(this.reconnectTimer)
        this.reconnectTimer = null
        this.reconnectAttempts = 0
        source.close()
        this.eventSource = null
        this.runSessionId = null
        this.runId = null
        this.running = false
        this.stopping = false
        this.cancelRequested = false
        this.streamContent = ''
        this.streamReasoning = null
        this.streamModelName = null
        this.clearActivity()
        this.refreshSession(sessionId)
        this.fetchSessions()
        useApprovalStore().fetchPending()
        if (expectedTitle) void this.refreshGeneratedTitle(sessionId, expectedTitle)
      }
      source.addEventListener('run.failed', (event) => {
        if (this.eventSource !== source) return
        const payload = JSON.parse(event.data)
        notifyError(payload.error, 'Die Agent-Anfrage ist fehlgeschlagen.')
        finish()
      })
      for (const name of ['run.completed', 'run.cancelled']) {
        source.addEventListener(name, () => {
          finish()
        })
      }
      source.onerror = async () => {
        if (this.eventSource !== source) return
        source.close()
        this.eventSource = null
        this.streamContent = ''
        this.streamReasoning = null
        if (finished) return
        this.running = true
        this.setActivity('reconnecting')
        this.reconnectAttempts += 1

        const stopDisconnectedRun = (error = null) => {
          if (this.runId !== runId) return
          clearTimeout(this.reconnectTimer)
          this.reconnectTimer = null
          this.runSessionId = null
          this.runId = null
          this.running = false
          this.stopping = false
          this.cancelRequested = false
          this.streamModelName = null
          this.clearActivity()
          if (error) notifyError(error, 'Die Agent-Anfrage ist fehlgeschlagen.')
          this.refreshSession(sessionId)
          this.fetchSessions()
          useApprovalStore().fetchPending()
        }
        const scheduleReconnect = (runState = {}) => {
          if (this.reconnectAttempts > 3) {
            notifyError({ code: 'stream.connectionLost' })
            stopDisconnectedRun()
            return
          }
          this.reconnectTimer = setTimeout(() => {
            if (this.selectedSessionId !== sessionId || this.runId !== runId) return
            this.watchRun(sessionId, runId, { ...runState, reconnecting: true })
          }, this.reconnectAttempts * 1000)
        }

        try {
          const { data } = await api.get(`/sessions/${sessionId}/runs/${runId}`, {
            suppressErrorToast: true
          })
          if (this.selectedSessionId !== sessionId || this.runId !== runId) return
          if (['queued', 'running', 'waiting_approval'].includes(data.status)) {
            scheduleReconnect(data)
          } else {
            stopDisconnectedRun(data.status === 'failed' ? data.error : null)
          }
        } catch {
          scheduleReconnect({ createdAt: this.activity.startedAt })
        }
      }
    }
  }
})
