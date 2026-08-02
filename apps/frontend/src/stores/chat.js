import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { api } from 'src/boot/axios'
import { useAgentIdentityStore } from 'src/stores/agent-identity'
import { useApprovalStore } from 'src/stores/approvals'
import { agentActivityDisplayKey } from 'src/utils/agent-activity'
import { safeAttachmentUrl } from 'src/utils/attachments'
import { notifyError } from 'src/utils/notifications'

const DRAFT_STORAGE_KEY = 'matoLoopChatDrafts'
const ACTIVE_TITLE_GENERATION_STATUSES = new Set(['pending', 'running'])
export const MAX_ATTACHMENT_DRAFTS = 4
export const MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024
export const MAX_ATTACHMENT_DRAFT_BYTES = 256 * 1024 * 1024

function normalizedMimeType(file) {
  return String(file?.type || '').split(';', 1)[0].trim().toLowerCase()
}

function attachmentKind(file) {
  const mimeType = normalizedMimeType(file)
  if (mimeType.startsWith('image/')) return 'image'
  if (mimeType.startsWith('audio/')) return 'audio'
  return 'file'
}

function attachmentPreviewUrl(file) {
  if (attachmentKind(file) !== 'image' || typeof globalThis.URL?.createObjectURL !== 'function') {
    return null
  }
  try {
    return globalThis.URL.createObjectURL(file)
  } catch {
    return null
  }
}

export async function downloadMessageAttachmentFiles(message) {
  return Promise.all((message?.attachments || []).map(async (attachment) => {
    const url = safeAttachmentUrl(attachment.url)
    if (!url) throw new Error('Invalid internal attachment URL')
    const { data } = await api.get(url, { responseType: 'blob', suppressErrorToast: true })
    const type = attachment.mimeType || data?.type || 'application/octet-stream'
    return new File([data], attachment.filename || 'datei', { type })
  }))
}

export function buildAttachmentFormData(payload, attachments) {
  const formData = new FormData()
  formData.append('payload', JSON.stringify(payload))
  for (const attachment of attachments) {
    formData.append('attachments', attachment.file, attachment.file.name || 'datei')
  }
  return formData
}

function defaultSessionOptions() {
  return {
    modelId: null,
    memoryMode: 'full',
    effectiveMemoryMode: 'full',
    memoryToolsAvailable: true,
    memoryUnavailableReason: null,
    webSearchPolicy: 'available',
    workingDirectory: null,
    workspaceAccess: 'read-only'
  }
}

function optionsFromSession(session) {
  return {
    modelId: session.modelId,
    memoryMode: session.memoryMode,
    effectiveMemoryMode: session.effectiveMemoryMode,
    memoryToolsAvailable: session.memoryToolsAvailable,
    memoryUnavailableReason: session.memoryUnavailableReason,
    webSearchPolicy: session.webSearchPolicy,
    workingDirectory: session.workingDirectory,
    workspaceAccess: session.workspaceAccess
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
    toolCallId: null,
    modelRequest: null,
    index: null,
    total: null,
    revision
  }
}

function sortSessions(sessions) {
  return [...sessions].sort((left, right) => {
    if (Boolean(left.pinned) !== Boolean(right.pinned)) return left.pinned ? -1 : 1
    const leftLastMessageAt = Date.parse(left.lastMessageAt || left.createdAt || '') || 0
    const rightLastMessageAt = Date.parse(right.lastMessageAt || right.createdAt || '') || 0
    return rightLastMessageAt - leftLastMessageAt
  })
}

function lastVisibleMessageAt(messages) {
  return [...messages]
    .reverse()
    .find((message) => ['user', 'assistant'].includes(message.role))
    ?.createdAt || null
}

function sessionBeforeUserMessage(session, messageId) {
  const index = session.messages?.findIndex((message) => message.id === messageId) ?? -1
  if (index < 0) return session
  const messages = session.messages.slice(0, index)
  return { ...session, messages, activeRun: null, lastMessageAt: lastVisibleMessageAt(messages) }
}

function sessionBeforeFailedResponse(session, messageId, activeRun) {
  const errorIndex = session.messages?.findIndex((message) => message.id === messageId) ?? -1
  if (errorIndex < 0) return { ...session, activeRun }
  let userIndex = errorIndex - 1
  while (userIndex >= 0 && session.messages[userIndex].role !== 'user') userIndex -= 1
  const messages = userIndex >= 0 ? session.messages.slice(0, userIndex + 1) : session.messages
  return { ...session, messages, activeRun, lastMessageAt: lastVisibleMessageAt(messages) }
}

async function sessionStateAfterMutation(sessionId, messageId) {
  try {
    const { data } = await api.get(`/sessions/${sessionId}`, { suppressErrorToast: true })
    return {
      deleted: false,
      messageRemoved: !data.messages?.some((message) => message.id === messageId),
      session: data
    }
  } catch (error) {
    if (error?.response?.status === 404) {
      return { deleted: true, messageRemoved: true, session: null }
    }
    throw error
  }
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
    historyActionPending: false,
    sessionRequestController: null,
    loadError: null,
    sessionDefaults: defaultSessionOptions(),
    drafts: loadDrafts(),
    attachmentDrafts: {
      newSession: [],
      sessionMessages: {}
    },
    audioDictations: {
      newSession: null,
      sessionMessages: {}
    }
  }),
  getters: {
    activeContext: (state) => state.current || state.drafts.newSession.options,
    activeDraft: (state) => state.current
      ? state.drafts.sessionMessages[state.current.id] || ''
      : state.drafts.newSession.message,
    activeAttachmentDraft: (state) => state.current
      ? state.attachmentDrafts.sessionMessages[state.current.id] || []
      : state.attachmentDrafts.newSession
  },
  actions: {
    setActivity(phase, details = {}) {
      const nextActivity = {
        phase,
        tool: details.tool || null,
        toolCallId: details.toolCallId || null,
        modelRequest: details.modelRequest || null,
        index: Number.isInteger(details.index) ? details.index : null,
        total: Number.isInteger(details.total) ? details.total : null
      }
      const sameDisplay = agentActivityDisplayKey(this.activity) === agentActivityDisplayKey(nextActivity)
      const startedAt = sameDisplay
        ? this.activity.startedAt || details.startedAt || new Date().toISOString()
        : details.startedAt || new Date().toISOString()
      this.activity = {
        ...nextActivity,
        startedAt,
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
        // Chat remains usable when local storage is blocked or full.
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
        } else {
          options.effectiveMemoryMode = 'disabled'
          options.memoryToolsAvailable = false
          options.memoryUnavailableReason = 'session.modelUnavailable'
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
    addAttachmentFiles(files) {
      const incoming = Array.from(files || [])
      if (this.activeAttachmentDraft.length + incoming.length > MAX_ATTACHMENT_DRAFTS) {
        return { added: 0, error: 'Du kannst höchstens vier Dateien pro Nachricht anhängen.' }
      }
      for (const file of incoming) {
        if (!Number.isFinite(file?.size) || file.size <= 0) {
          return { added: 0, error: 'Die ausgewählte Datei ist leer.' }
        }
        if (file.size > MAX_ATTACHMENT_BYTES) {
          return { added: 0, error: 'Eine Datei darf höchstens 64 MiB groß sein.' }
        }
      }
      const draftBytes = [
        ...this.attachmentDrafts.newSession,
        ...Object.values(this.attachmentDrafts.sessionMessages).flat()
      ].reduce((total, attachment) => total + (Number(attachment.file?.size) || 0), 0)
      const incomingBytes = incoming.reduce((total, file) => total + file.size, 0)
      if (draftBytes + incomingBytes > MAX_ATTACHMENT_DRAFT_BYTES) {
        return {
          added: 0,
          error: 'Dateianhänge dürfen über alle Sessions zusammen höchstens 256 MiB belegen.'
        }
      }
      if (this.current && !this.attachmentDrafts.sessionMessages[this.current.id]) {
        this.attachmentDrafts.sessionMessages[this.current.id] = []
      }
      const target = this.activeAttachmentDraft
      for (const file of incoming) {
        target.push({
          id: globalThis.crypto?.randomUUID?.() || `attachment-${Date.now()}-${target.length}`,
          file: markRaw(file),
          kind: attachmentKind(file),
          mimeType: normalizedMimeType(file),
          objectUrl: attachmentPreviewUrl(file)
        })
      }
      return { added: incoming.length, error: null }
    },
    removeAttachmentFile(id) {
      const target = this.activeAttachmentDraft
      const index = target.findIndex((attachment) => attachment.id === id)
      if (index < 0) return
      const [attachment] = target.splice(index, 1)
      if (attachment.objectUrl) globalThis.URL?.revokeObjectURL?.(attachment.objectUrl)
    },
    clearAttachmentDraft(sessionId = this.current?.id || null) {
      const target = sessionId
        ? this.attachmentDrafts.sessionMessages[sessionId] || []
        : this.attachmentDrafts.newSession
      for (const attachment of target) {
        if (attachment.objectUrl) globalThis.URL?.revokeObjectURL?.(attachment.objectUrl)
      }
      if (sessionId) delete this.attachmentDrafts.sessionMessages[sessionId]
      else this.attachmentDrafts.newSession = []
    },
    clearAllAttachmentDrafts() {
      for (const attachment of [
        ...this.attachmentDrafts.newSession,
        ...Object.values(this.attachmentDrafts.sessionMessages).flat()
      ]) {
        if (attachment.objectUrl) globalThis.URL?.revokeObjectURL?.(attachment.objectUrl)
      }
      this.attachmentDrafts = { newSession: [], sessionMessages: {} }
      this.audioDictations = { newSession: null, sessionMessages: {} }
    },
    attachmentReplacementError(files, sessionId, sourceSessionId = sessionId) {
      if (files.length > MAX_ATTACHMENT_DRAFTS) {
        return 'Du kannst höchstens vier Dateien pro Nachricht anhängen.'
      }
      for (const file of files) {
        if (!Number.isFinite(file?.size) || file.size <= 0) {
          return 'Die wiederhergestellte Datei ist leer.'
        }
        if (file.size > MAX_ATTACHMENT_BYTES) {
          return 'Eine Datei darf höchstens 64 MiB groß sein.'
        }
      }
      const replaced = [
        ...(sessionId
          ? this.attachmentDrafts.sessionMessages[sessionId] || []
          : this.attachmentDrafts.newSession),
        ...(sourceSessionId && sourceSessionId !== sessionId
          ? this.attachmentDrafts.sessionMessages[sourceSessionId] || []
          : [])
      ]
      const currentBytes = [
        ...this.attachmentDrafts.newSession,
        ...Object.values(this.attachmentDrafts.sessionMessages).flat()
      ].reduce((total, attachment) => total + (Number(attachment.file?.size) || 0), 0)
      const replacedBytes = replaced.reduce(
        (total, attachment) => total + (Number(attachment.file?.size) || 0),
        0
      )
      const incomingBytes = files.reduce((total, file) => total + file.size, 0)
      return currentBytes - replacedBytes + incomingBytes > MAX_ATTACHMENT_DRAFT_BYTES
        ? 'Dateianhänge dürfen über alle Sessions zusammen höchstens 256 MiB belegen.'
        : null
    },
    replaceAttachmentFiles(files, sessionId) {
      this.clearAttachmentDraft(sessionId)
      const target = files.map((file, index) => ({
        id: globalThis.crypto?.randomUUID?.() || `attachment-${Date.now()}-${index}`,
        file: markRaw(file),
        kind: attachmentKind(file),
        mimeType: normalizedMimeType(file),
        objectUrl: attachmentPreviewUrl(file)
      }))
      if (sessionId) this.attachmentDrafts.sessionMessages[sessionId] = target
      else this.attachmentDrafts.newSession = target
    },
    getAudioDictation(sessionId = null) {
      return sessionId
        ? this.audioDictations.sessionMessages[sessionId] || null
        : this.audioDictations.newSession
    },
    setAudioDictation(entry, sessionId = null) {
      const value = entry ? { ...entry, file: markRaw(entry.file) } : null
      if (sessionId) {
        if (value) this.audioDictations.sessionMessages[sessionId] = value
        else delete this.audioDictations.sessionMessages[sessionId]
      } else {
        this.audioDictations.newSession = value
      }
    },
    resetNewDraft() {
      this.clearAttachmentDraft(null)
      this.drafts.newSession = {
        message: '',
        options: { ...this.sessionDefaults }
      }
      this.persistDrafts()
    },
    async fetchSessions() {
      this.sessions = sortSessions((await api.get('/sessions')).data)
      const sessionIds = new Set(this.sessions.map((session) => session.id))
      for (const id of Object.keys(this.drafts.sessionMessages)) {
        if (!sessionIds.has(id)) delete this.drafts.sessionMessages[id]
      }
      for (const id of Object.keys(this.attachmentDrafts.sessionMessages)) {
        if (!sessionIds.has(id)) this.clearAttachmentDraft(id)
      }
      for (const id of Object.keys(this.audioDictations.sessionMessages)) {
        if (!sessionIds.has(id)) delete this.audioDictations.sessionMessages[id]
      }
      this.persistDrafts()
    },
    async refreshGeneratedTitle(sessionId) {
      for (const delay of [1000, 2000, 4000, 8000, 15000, 30000]) {
        await new Promise((resolve) => setTimeout(resolve, delay))
        const localSession = this.current?.id === sessionId
          ? this.current
          : this.sessions.find((item) => item.id === sessionId)
        if (!ACTIVE_TITLE_GENERATION_STATUSES.has(localSession?.titleGenerationStatus)) return
        try {
          await this.fetchSessions()
        } catch {
          continue
        }
        const session = this.sessions.find((item) => item.id === sessionId)
        if (!session) continue
        if (this.current?.id === sessionId) {
          this.current.title = session.title
          this.current.titleGenerationStatus = session.titleGenerationStatus
        }
        if (!ACTIVE_TITLE_GENERATION_STATUSES.has(session.titleGenerationStatus)) return
      }
    },
    async loadSession(id) {
      this.sessionRequestController?.abort()
      this.selectedSessionId = id
      this.sessionLoadVersion += 1
      const loadVersion = this.sessionLoadVersion
      const controller = id ? markRaw(new AbortController()) : null
      this.sessionRequestController = controller
      this.current = null
      this.loadError = null
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
        this.loading = false
        return
      }
      this.loading = true
      try {
        const session = (await api.get(`/sessions/${id}`, { signal: controller.signal })).data
        if (this.selectedSessionId !== id || this.sessionLoadVersion !== loadVersion) return
        this.current = session
        if (session.activeRun && this.runId !== session.activeRun.id) {
          this.watchRun(id, session.activeRun.id, session.activeRun)
        } else if (!session.activeRun && !this.running) {
          this.clearActivity()
        }
      } catch (error) {
        if (controller.signal.aborted || this.sessionLoadVersion !== loadVersion) return
        this.loadError = error
        throw error
      } finally {
        if (this.sessionRequestController === controller) this.sessionRequestController = null
        if (this.sessionLoadVersion === loadVersion) this.loading = false
      }
    },
    async refreshSession(id) {
      if (this.selectedSessionId !== id || this.loading) return
      this.sessionRequestController?.abort()
      this.sessionLoadVersion += 1
      const loadVersion = this.sessionLoadVersion
      const controller = markRaw(new AbortController())
      this.sessionRequestController = controller
      try {
        const session = (await api.get(`/sessions/${id}`, { signal: controller.signal })).data
        if (this.selectedSessionId === id && this.sessionLoadVersion === loadVersion) {
          this.current = session
          this.loadError = null
          if (session.activeRun && this.runId !== session.activeRun.id) {
            this.watchRun(id, session.activeRun.id, session.activeRun)
          } else if (!session.activeRun && !this.running) {
            this.clearActivity()
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) throw error
      } finally {
        if (this.sessionRequestController === controller) this.sessionRequestController = null
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
        }
      }
      this.persistDrafts()
    },
    async updateCurrent(patch) {
      const session = this.current
      if (!session) return
      const { data } = await api.patch(`/sessions/${session.id}`, patch)
      if (this.current?.id === session.id && this.selectedSessionId === session.id) {
        this.current = { ...session, ...data }
      }
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
      this.clearAttachmentDraft(id)
      this.setAudioDictation(null, id)
      this.persistDrafts()
      if (this.current?.id === id) this.current = null
    },
    clearRunTracking() {
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
    },
    updateSessionSummary(session) {
      const summary = { ...session }
      delete summary.messages
      delete summary.activeRun
      const existing = this.sessions.some((item) => item.id === session.id)
      this.sessions = sortSessions(existing
        ? this.sessions.map((item) => item.id === session.id ? { ...item, ...summary } : item)
        : [...this.sessions, summary])
    },
    applySessionResult(session) {
      this.current = session
      this.updateSessionSummary(session)
    },
    async undoUserMessage(message) {
      const session = this.current
      if (!session || message?.role !== 'user' || this.historyActionPending) return null
      this.historyActionPending = true
      try {
        let files
        try {
          files = await downloadMessageAttachmentFiles(message)
        } catch (error) {
          notifyError(error, 'Die Anhänge konnten nicht wiederhergestellt werden.')
          throw error
        }
        const deletesSession = session.messages?.[0]?.id === message.id
        const replacementError = this.attachmentReplacementError(
          files,
          deletesSession ? null : session.id,
          session.id
        )
        if (replacementError) {
          notifyError({}, replacementError)
          throw new Error(replacementError)
        }
        const options = optionsFromSession(session)
        let data
        let recoveredSession = null
        let mutationKnownUnchanged = false
        try {
          data = (await api.post(`/sessions/${session.id}/messages/${message.id}/undo`, null, {
            suppressErrorToast: true
          })).data
        } catch (error) {
          const ambiguous = !error?.response || error.response.status >= 500
          if (ambiguous) {
            try {
              const recovered = await sessionStateAfterMutation(session.id, message.id)
              if (recovered.messageRemoved) {
                recoveredSession = recovered.session
                data = {
                  sessionDeleted: recovered.deleted,
                  draft: { content: message.content }
                }
              } else {
                mutationKnownUnchanged = true
              }
            } catch {
              // Keep the downloaded files recoverable when the server state cannot be determined.
            }
          }
          if (!data && ambiguous && !mutationKnownUnchanged) {
            if (deletesSession) {
              this.drafts.newSession = { message: message.content, options }
              this.replaceAttachmentFiles(files, null)
            } else {
              this.drafts.sessionMessages[session.id] = message.content
              this.replaceAttachmentFiles(files, session.id)
            }
            this.persistDrafts()
          }
          if (!data) {
            notifyError(error)
            throw error
          }
        }
        const stillSelected = this.selectedSessionId === session.id && this.current?.id === session.id
        const updatedSession = recoveredSession || sessionBeforeUserMessage(
          stillSelected ? this.current : session,
          message.id
        )
        if (stillSelected) {
          this.sessionRequestController?.abort()
          this.sessionLoadVersion += 1
          this.clearRunTracking()
        }
        delete this.drafts.sessionMessages[session.id]
        this.clearAttachmentDraft(session.id)
        this.setAudioDictation(null, session.id)
        if (data.sessionDeleted) {
          this.sessions = this.sessions.filter((item) => item.id !== session.id)
          if (stillSelected) {
            this.current = null
            this.selectedSessionId = null
          }
          this.drafts.newSession = { message: data.draft.content, options }
          this.replaceAttachmentFiles(files, null)
        } else {
          if (stillSelected) this.applySessionResult(updatedSession)
          else this.updateSessionSummary(updatedSession)
          this.drafts.sessionMessages[session.id] = data.draft.content
          this.replaceAttachmentFiles(files, session.id)
          if (stillSelected && !recoveredSession) {
            void this.refreshSession(session.id).catch(() => {})
          }
        }
        this.persistDrafts()
        void useApprovalStore().fetchPending()
        return data
      } finally {
        this.historyActionPending = false
      }
    },
    async retryFailedMessage(message) {
      const session = this.current
      if (!session || message?.kind !== 'runtime_error' || this.historyActionPending) return null
      this.historyActionPending = true
      try {
        let data
        let recoveredSession = null
        try {
          data = (await api.post(`/sessions/${session.id}/messages/${message.id}/retry`, null, {
            suppressErrorToast: true
          })).data
        } catch (error) {
          const ambiguous = !error?.response || error.response.status >= 500
          if (ambiguous) {
            try {
              const recovered = await sessionStateAfterMutation(session.id, message.id)
              if (recovered.messageRemoved && recovered.session) {
                recoveredSession = recovered.session
                data = {
                  runId: recovered.session.activeRun?.id || null,
                  modelName: recovered.session.activeRun?.modelName || null,
                  createdAt: recovered.session.activeRun?.createdAt || null
                }
              }
            } catch {
              // The original request error remains canonical when recovery is unavailable.
            }
          }
          if (!data) {
            notifyError(error)
            throw error
          }
        }
        const activeRun = data.runId ? {
          id: data.runId,
          status: 'queued',
          modelName: data.modelName,
          createdAt: data.createdAt
        } : null
        const stillSelected = this.selectedSessionId === session.id && this.current?.id === session.id
        const updatedSession = recoveredSession || sessionBeforeFailedResponse(
          stillSelected ? this.current : session,
          message.id,
          activeRun
        )
        if (stillSelected) {
          this.sessionRequestController?.abort()
          this.sessionLoadVersion += 1
          this.clearRunTracking()
          this.applySessionResult(updatedSession)
          if (activeRun) this.watchRun(session.id, data.runId, activeRun)
        } else {
          this.updateSessionSummary(updatedSession)
        }
        void useApprovalStore().fetchPending()
        return data
      } finally {
        this.historyActionPending = false
      }
    },
    async send(content) {
      if (this.running || this.historyActionPending) return null
      const attachments = [...this.activeAttachmentDraft]
      return this.current
        ? this.sendToCurrent(content, attachments)
        : this.startSession(content, attachments)
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
    async startSession(content, attachments = []) {
      const sessionLoadVersion = this.sessionLoadVersion
      const selectedSessionId = this.selectedSessionId
      this.running = true
      this.streamContent = ''
      this.streamReasoning = null
      this.streamModelName = null
      this.setActivity('submitting', { startedAt: new Date().toISOString() })
      const options = this.drafts.newSession.options
      try {
        const payload = {
          content,
          model_id: options.modelId,
          memory_mode: options.memoryMode,
          web_search_policy: options.webSearchPolicy,
          working_directory: options.workingDirectory,
          workspace_access: options.workspaceAccess
        }
        const { data } = attachments.length
          ? await api.post('/sessions/with-attachments', buildAttachmentFormData(payload, attachments))
          : await api.post('/sessions', payload)
        const userMessage = data.message || { id: `local-${Date.now()}`, role: 'user', content }
        const session = { ...data.session, messages: [userMessage] }
        const stillSelected = this.sessionLoadVersion === sessionLoadVersion
          && this.selectedSessionId === selectedSessionId
          && !this.current
        this.sessions = [data.session, ...this.sessions.filter((item) => item.id !== session.id)]
        this.resetNewDraft()
        if (stillSelected) {
          this.current = session
          this.selectedSessionId = session.id
          this.watchRun(session.id, data.runId, { status: 'queued', modelName: data.modelName })
          await this.cancelActiveRun()
        }
        return { created: stillSelected, sessionId: session.id }
      } catch (error) {
        if (
          this.sessionLoadVersion === sessionLoadVersion
          && this.selectedSessionId === selectedSessionId
        ) {
          this.running = false
          this.stopping = false
          this.cancelRequested = false
          this.clearActivity()
        }
        throw error
      }
    },
    async sendToCurrent(content, attachments = []) {
      const session = this.current
      const sessionLoadVersion = this.sessionLoadVersion
      this.running = true
      this.streamContent = ''
      this.streamReasoning = null
      this.streamModelName = null
      this.setActivity('submitting', { startedAt: new Date().toISOString() })
      const localMessage = {
        id: `local-${Date.now()}`,
        role: 'user',
        content,
        createdAt: new Date().toISOString()
      }
      session.messages.push(localMessage)
      try {
        const payload = { content }
        const { data } = attachments.length
          ? await api.post(
              `/sessions/${session.id}/messages/with-attachments`,
              buildAttachmentFormData(payload, attachments)
            )
          : await api.post(`/sessions/${session.id}/messages`, payload)
        if (data.message) {
          const index = session.messages.findIndex((message) => message.id === localMessage.id)
          if (index >= 0) session.messages[index] = data.message
          if (data.message.createdAt) {
            session.lastMessageAt = data.message.createdAt
            this.sessions = sortSessions(
              this.sessions.map((item) => item.id === session.id
                ? { ...item, lastMessageAt: data.message.createdAt }
                : item)
            )
          }
        }
        this.clearAttachmentDraft(session.id)
        delete this.drafts.sessionMessages[session.id]
        this.persistDrafts()
        if (
          this.sessionLoadVersion === sessionLoadVersion
          && this.selectedSessionId === session.id
          && this.current?.id === session.id
        ) {
          this.watchRun(session.id, data.runId, { status: 'queued', modelName: data.modelName })
          await this.cancelActiveRun()
        } else {
          void this.fetchSessions().catch(() => {})
        }
        return { created: false, sessionId: session.id }
      } catch (error) {
        session.messages = session.messages.filter((message) => message.id !== localMessage.id)
        if (
          this.sessionLoadVersion === sessionLoadVersion
          && this.selectedSessionId === session.id
          && this.current?.id === session.id
        ) {
          this.running = false
          this.stopping = false
          this.cancelRequested = false
          this.clearActivity()
        }
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
        if (this.current?.id === payload.sessionId) {
          this.current.title = payload.title
          this.current.titleGenerationStatus = 'completed'
        }
        const index = this.sessions.findIndex((session) => session.id === payload.sessionId)
        if (index >= 0) {
          this.sessions[index] = {
            ...this.sessions[index],
            title: payload.title,
            titleGenerationStatus: 'completed'
          }
        }
      })
      source.addEventListener('agent.identity.updated', (event) => {
        if (this.eventSource !== source) return
        useAgentIdentityStore().assignIdentity(JSON.parse(event.data).identity)
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
        this.setActivity('waiting_approval', {
          tool: approval.details?.tool,
          toolCallId: approval.details?.toolCallId
        })
        useApprovalStore().add(approval)
      })
      source.addEventListener('approval.resolved', () => {
        if (this.eventSource !== source) return
        this.setActivity('processing_tool', {
          tool: this.activity.tool,
          toolCallId: this.activity.toolCallId
        })
        useApprovalStore().fetchPending()
      })
      source.addEventListener('approval.expired', () => {
        if (this.eventSource !== source) return
        this.setActivity('processing_tool', {
          tool: this.activity.tool,
          toolCallId: this.activity.toolCallId
        })
        useApprovalStore().fetchPending()
      })
      const finish = () => {
        if (this.eventSource !== source) return
        const current = this.current?.id === sessionId ? this.current : null
        const titleGenerationActive = ACTIVE_TITLE_GENERATION_STATUSES.has(
          current?.titleGenerationStatus
        )
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
        void useAgentIdentityStore().fetchIdentity({ suppressErrorToast: true }).catch(() => {})
        if (titleGenerationActive) void this.refreshGeneratedTitle(sessionId)
      }
      source.addEventListener('run.failed', () => {
        if (this.eventSource !== source) return
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

        const stopDisconnectedRun = () => {
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
            stopDisconnectedRun()
          }
        } catch {
          scheduleReconnect({ createdAt: this.activity.startedAt })
        }
      }
    }
  }
})
