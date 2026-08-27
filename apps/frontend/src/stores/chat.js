import { defineStore } from 'pinia'
import { markRaw, toRaw } from 'vue'
import { api } from 'src/boot/axios'
import { useAgentIdentityStore } from 'src/stores/agent-identity'
import { useApprovalStore } from 'src/stores/approvals'
import { controlCenterRequestState, useControlCenterStore } from 'src/stores/control-center'
import { useExecutionAgentStore } from 'src/stores/execution-agents'
import { useInstanceLimitsStore } from 'src/stores/instance-limits'
import { useQuestionStore } from 'src/stores/questions'
import { useWorkspaceStore } from 'src/stores/workspace'
import {
  attachmentKind,
  attachmentPreviewUrl,
  buildAttachmentFormData,
  downloadMessageAttachmentFiles,
  MAX_ATTACHMENT_DRAFT_BYTES,
  normalizedMimeType,
  releaseAttachmentDrafts
} from 'src/features/chat/attachments'
import {
  clearPersistedDrafts,
  defaultSessionOptions,
  emptyActivity,
  emptyDrafts,
  emptySessionList,
  groupedOutbox,
  lastAssistantMessage,
  loadDrafts,
  newSessionQueueKey,
  normalizedNewSessionDraft,
  optionsFromSession,
  persistDrafts,
  queueKeyForEntry,
  sanitizedSessionOptions,
  sessionBeforeFailedResponse,
  sessionBeforeUserMessage,
  sessionScopeKey,
  sessionScopeParams,
  sortSessions
} from 'src/features/chat/state'
import { agentActivityDisplayKey } from 'src/utils/agent-activity'
import { errorDescriptor } from 'src/utils/api-errors'
import {
  managedSessionFileTarget,
  toolFileActions
} from 'src/utils/attachments'
import {
  clearChatOutbox,
  deleteChatOutboxEntry,
  deleteChatOutboxEntries,
  loadChatOutbox,
  saveChatOutboxEntries,
  saveChatOutboxEntry
} from 'src/utils/chat-outbox'
import { notifyError } from 'src/utils/notifications'

const SESSION_PAGE_SIZE = 20
const ACTIVE_TITLE_GENERATION_STATUSES = new Set(['pending', 'running'])
const FILESYSTEM_MUTATION_TOOLS = new Set([
  'write_file',
  'edit_file',
  'create_directory',
  'delete_path',
  'copy_path',
  'move_path'
])
const OUTBOX_RETRY_MS = 4000

function mergeMessages(existing = [], incoming = []) {
  const replacements = new Map(incoming.map((message) => [message.id, message]))
  const merged = existing.map((message) => replacements.get(message.id) || message)
  const existingIds = new Set(existing.map((message) => message.id))
  merged.push(...incoming.filter((message) => !existingIds.has(message.id)))
  return merged
}

function newSubmissionId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  const bytes = globalThis.crypto?.getRandomValues?.(new Uint8Array(16))
  if (!bytes) throw new Error('Secure random UUID generation is unavailable')
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const value = [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`
}

async function sessionStateAfterMutation(sessionId, messageId) {
  try {
    await api.get(`/sessions/${sessionId}/messages/${messageId}`, { suppressErrorToast: true })
    return {
      deleted: false,
      messageRemoved: false,
      session: null
    }
  } catch (error) {
    if (error?.response?.status !== 404) throw error
  }
  try {
    const { data } = await api.get(`/sessions/${sessionId}`, { suppressErrorToast: true })
    return { deleted: false, messageRemoved: true, session: data }
  } catch (error) {
    if (error?.response?.status === 404) return { deleted: true, messageRemoved: true, session: null }
    throw error
  }
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    sessionLists: { standalone: emptySessionList() },
    sessionCatalog: [],
    sessionCatalogVersion: 0,
    sessionsProjectId: null,
    current: null,
    loading: false,
    running: false,
    stopping: false,
    cancelRequested: false,
    streamContent: '',
    streamReasoning: null,
    streamModelName: null,
    processOutputs: {},
    eventSource: null,
    selectedSessionId: null,
    runSessionId: null,
    runId: null,
    reconnectTimer: null,
    reconnectAttempts: 0,
    activity: emptyActivity(),
    sessionLoadVersion: 0,
    historyActionPending: false,
    sessionSettingsPending: 0,
    sessionRequestController: null,
    sessionRefreshPromise: null,
    olderMessagesLoading: false,
    olderMessagesLoadFailed: false,
    olderMessagesPromise: null,
    loadError: null,
    sessionDefaults: defaultSessionOptions(),
    draftOwnerUserId: null,
    drafts: emptyDrafts(),
    attachmentDrafts: {
      newSession: [],
      newSessions: { standalone: [] },
      sessionMessages: {}
    },
    audioDictations: {
      newSession: null,
      newSessions: { standalone: null },
      sessionMessages: {}
    },
    queuedMessages: {},
    queuedSendPausedSessions: {},
    outboxLoaded: false,
    outboxLoadFailed: false,
    outboxSaving: false,
    outboxLoadVersion: 0,
    outboxLoadPromise: null,
    queueDrainPromise: null,
    queueDrainTimer: null,
    queueSendingEntryId: null,
    createdSessionResult: null
  }),
  getters: {
    activeSessionList: (state) => state.sessionLists[sessionScopeKey(state.sessionsProjectId)]
      || emptySessionList(),
    sessions() {
      return this.activeSessionList.items
    },
    sessionsLoaded() {
      return this.activeSessionList.loaded
    },
    sessionsNextCursor() {
      return this.activeSessionList.nextCursor
    },
    sessionsLoadingMore() {
      return this.activeSessionList.loadingMore
    },
    sessionsLoadMoreFailed() {
      return this.activeSessionList.loadMoreFailed
    },
    cachedSessions: (state) => {
      const sessions = new Map(state.sessionCatalog.map((session) => [session.id, session]))
      for (const list of Object.values(state.sessionLists)) {
        for (const session of list.items) sessions.set(session.id, session)
      }
      return [...sessions.values()]
    },
    sessionList: (state) => (projectId = null) => (
      state.sessionLists[sessionScopeKey(projectId)] || null
    ),
    activeContext: (state) => state.current || state.drafts.newSession.options,
    activeDraft: (state) => state.current
      ? state.drafts.sessionMessages[state.current.id] || ''
      : state.drafts.newSession.message,
    activeAttachmentDraft: (state) => state.current
      ? state.attachmentDrafts.sessionMessages[state.current.id] || []
      : state.attachmentDrafts.newSession,
    activeQueueKey: (state) => state.current?.id || newSessionQueueKey(state.sessionsProjectId),
    activeQueuedMessages() {
      return this.queuedMessages[this.activeQueueKey] || []
    },
    queuedSendPaused() {
      return Boolean(this.queuedSendPausedSessions[this.activeQueueKey])
    },
    messageQueueActive() {
      if (this.activeQueuedMessages.length) return true
      if (!this.current) return false
      if (this.running || this.stopping || this.runId) return true
      const sessionId = this.current.id
      return Boolean(
        useApprovalStore().forSession(sessionId).length
        || useQuestionStore().forSession(sessionId).length
      )
    },
    queuedSendReady() {
      if (!this.outboxLoaded || this.queuedSendPaused) return false
      if (this.loading || this.sessionRequestController || this.running || this.stopping || this.runId) return false
      if (this.historyActionPending || this.sessionSettingsPending > 0) return false
      if (!this.activeQueuedMessages.length) return false
      const sessionId = this.current?.id
      if (!sessionId) return this.selectedSessionId === null
      if (useApprovalStore().forSession(sessionId).length) return false
      if (useQuestionStore().forSession(sessionId).length) return false
      return true
    }
  },
  actions: {
    ensureSessionList(projectId = null) {
      const scope = sessionScopeKey(projectId)
      if (!this.sessionLists[scope]) this.sessionLists[scope] = emptySessionList()
      return this.sessionLists[scope]
    },
    findCachedSession(id) {
      return this.cachedSessions.find((session) => session.id === id) || null
    },
    updateCachedSession(id, update) {
      for (const list of Object.values(this.sessionLists)) {
        const index = list.items.findIndex((session) => session.id === id)
        if (index < 0) continue
        list.version += 1
        list.items[index] = update(list.items[index])
        list.items = sortSessions(list.items)
      }
      const catalogIndex = this.sessionCatalog.findIndex((session) => session.id === id)
      if (catalogIndex >= 0) {
        this.sessionCatalogVersion += 1
        this.sessionCatalog[catalogIndex] = update(this.sessionCatalog[catalogIndex])
        this.sessionCatalog = sortSessions(this.sessionCatalog)
      }
    },
    removeCachedSession(id) {
      for (const list of Object.values(this.sessionLists)) {
        if (!list.items.some((session) => session.id === id)) continue
        list.version += 1
        list.items = list.items.filter((session) => session.id !== id)
      }
      if (this.sessionCatalog.some((session) => session.id === id)) this.sessionCatalogVersion += 1
      this.sessionCatalog = this.sessionCatalog.filter((session) => session.id !== id)
    },
    async prepareProjectOutboxDeletion(projectId, sessionIds = []) {
      if (this.outboxLoadPromise) await this.outboxLoadPromise
      if (!this.outboxLoaded || this.outboxLoadFailed) {
        notifyError({}, 'Vorgemerkte Nachrichten konnten nicht geladen werden.')
        throw new Error('Chat outbox is unavailable')
      }
      this.cancelQueueDrain()
      if (this.queueDrainPromise) await this.queueDrainPromise
      this.cancelQueueDrain()
      const ids = new Set(sessionIds)
      const entries = Object.values(this.queuedMessages).flat().filter((entry) => (
        entry.projectId === projectId || ids.has(entry.sessionId)
      ))
      const snapshot = {
        entries: [...entries],
        ownerUserId: this.draftOwnerUserId,
        loadVersion: this.outboxLoadVersion
      }
      if (!entries.length) return snapshot
      try {
        await deleteChatOutboxEntries(entries.map((entry) => entry.id))
      } catch (error) {
        this.scheduleQueueDrain(0)
        notifyError({}, 'Vorgemerkte Nachrichten konnten nicht gelöscht werden.')
        throw error
      }
      const entryIds = new Set(entries.map((entry) => entry.id))
      for (const [key, queue] of Object.entries(this.queuedMessages)) {
        const remaining = queue.filter((entry) => !entryIds.has(entry.id))
        if (remaining.length) this.queuedMessages[key] = remaining
        else delete this.queuedMessages[key]
        if (remaining[0]?.paused) this.queuedSendPausedSessions[key] = true
        else delete this.queuedSendPausedSessions[key]
      }
      return snapshot
    },
    async restoreProjectOutboxDeletion(snapshot) {
      if (
        snapshot?.entries.length
        && snapshot.ownerUserId === this.draftOwnerUserId
        && snapshot.loadVersion === this.outboxLoadVersion
      ) {
        try {
          await saveChatOutboxEntries(snapshot.entries.map((entry) => toRaw(entry)))
        } catch {
          notifyError({}, 'Vorgemerkte Nachrichten konnten nicht wiederhergestellt werden.')
        }
        for (const entry of snapshot.entries) {
          const key = queueKeyForEntry(entry)
          const queue = this.queuedMessages[key] || []
          if (!queue.some((item) => item.id === entry.id)) queue.push(entry)
          queue.sort((left, right) => left.queuedAt.localeCompare(right.queuedAt))
          this.queuedMessages[key] = queue
          if (queue[0]?.paused) this.queuedSendPausedSessions[key] = true
          else delete this.queuedSendPausedSessions[key]
        }
      }
      this.scheduleQueueDrain(0)
    },
    async removeProjectScope(projectId, projectSessionIds = [], outboxDeletion = null) {
      if (this.outboxLoadPromise) await this.outboxLoadPromise
      const scope = sessionScopeKey(projectId)
      this.sessionCatalogVersion += 1
      const sessionIds = new Set(projectSessionIds)
      for (const session of this.cachedSessions) {
        if (session.projectId === projectId) sessionIds.add(session.id)
      }
      if (this.current?.projectId === projectId) sessionIds.add(this.current.id)
      for (const entries of Object.values(this.queuedMessages)) {
        for (const entry of entries) {
          if (entry.projectId === projectId && entry.sessionId) sessionIds.add(entry.sessionId)
        }
      }
      if (this.sessionsProjectId === projectId) {
        if (this.selectedSessionId) sessionIds.add(this.selectedSessionId)
        if (this.runSessionId) sessionIds.add(this.runSessionId)
      }

      this.cancelQueueDrain()
      const queueKeys = Object.entries(this.queuedMessages)
        .filter(([key, entries]) => (
          key === newSessionQueueKey(projectId)
          || entries.some((entry) => entry.projectId === projectId || sessionIds.has(entry.sessionId))
        ))
        .map(([key]) => key)
      for (const key of queueKeys) {
        releaseAttachmentDrafts((this.queuedMessages[key] || []).flatMap((entry) => entry.attachments))
        delete this.queuedMessages[key]
        delete this.queuedSendPausedSessions[key]
      }

      const activeSessionAffected = sessionIds.has(this.current?.id)
        || sessionIds.has(this.selectedSessionId)
        || sessionIds.has(this.runSessionId)
      if (activeSessionAffected) await this.loadSession(null)
      for (const id of sessionIds) {
        this.removeCachedSession(id)
        delete this.drafts.sessionMessages[id]
        this.clearAttachmentDraft(id)
        this.setAudioDictation(null, id)
      }
      for (const attachment of this.attachmentDrafts.newSessions[scope] || []) {
        if (attachment.objectUrl) globalThis.URL?.revokeObjectURL?.(attachment.objectUrl)
      }
      delete this.sessionLists[scope]
      delete this.drafts.newSessions[scope]
      delete this.attachmentDrafts.newSessions[scope]
      delete this.audioDictations.newSessions[scope]
      if (this.sessionsProjectId === projectId) {
        this.sessionsProjectId = null
        this.ensureSessionList(null)
        if (!this.drafts.newSessions.standalone) {
          this.drafts.newSessions.standalone = normalizedNewSessionDraft(null)
        }
        if (!this.attachmentDrafts.newSessions.standalone) {
          this.attachmentDrafts.newSessions.standalone = []
        }
        if (!Object.hasOwn(this.audioDictations.newSessions, 'standalone')) {
          this.audioDictations.newSessions.standalone = null
        }
        this.drafts.newSession = this.drafts.newSessions.standalone
        this.attachmentDrafts.newSession = this.attachmentDrafts.newSessions.standalone
        this.audioDictations.newSession = this.audioDictations.newSessions.standalone
      }
      this.persistDrafts()
      releaseAttachmentDrafts((outboxDeletion?.entries || []).flatMap((entry) => entry.attachments))
      if (Object.values(this.queuedMessages).some((queue) => queue.length)) {
        this.scheduleQueueDrain(0)
      }
    },
    setActivity(phase, details = {}) {
      const nextActivity = {
        phase,
        tool: details.tool || null,
        toolCallId: details.toolCallId || null,
        modelRequest: details.modelRequest || null,
        providerAttempt: Number.isInteger(details.providerAttempt) ? details.providerAttempt : null,
        providerAttemptTotal: Number.isInteger(details.providerAttemptTotal)
          ? details.providerAttemptTotal
          : null,
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
      if (!this.draftOwnerUserId || typeof window === 'undefined') return
      try {
        this.drafts.newSessions[sessionScopeKey(this.sessionsProjectId)] = this.drafts.newSession
        persistDrafts(this.draftOwnerUserId, this.drafts)
      } catch {
        // Chat remains usable when local storage is blocked or full.
      }
    },
    setDraftOwner(userId) {
      const ownerUserId = typeof userId === 'string' && userId ? userId : null
      if (ownerUserId === this.draftOwnerUserId) return
      this.cancelQueueDrain()
      this.clearAllAttachmentDrafts()
      this.draftOwnerUserId = ownerUserId
      this.queuedMessages = {}
      this.queuedSendPausedSessions = {}
      this.outboxLoaded = !ownerUserId
      this.outboxLoadFailed = false
      const loadVersion = ++this.outboxLoadVersion
      this.outboxLoadPromise = null
      this.drafts = loadDrafts(ownerUserId)
      const scope = sessionScopeKey(this.sessionsProjectId)
      if (!this.drafts.newSessions[scope]) {
        this.drafts.newSessions[scope] = normalizedNewSessionDraft(null)
      }
      this.drafts.newSession = this.drafts.newSessions[scope]
      if (!this.attachmentDrafts.newSessions[scope]) this.attachmentDrafts.newSessions[scope] = []
      this.attachmentDrafts.newSession = this.attachmentDrafts.newSessions[scope]
      this.audioDictations.newSessions[scope] = null
      this.audioDictations.newSession = this.audioDictations.newSessions[scope]
      if (ownerUserId) {
        this.persistDrafts()
        const request = this.loadQueuedMessages(ownerUserId, loadVersion)
        this.outboxLoadPromise = markRaw(request)
        void request.finally(() => {
          if (this.outboxLoadPromise === request) this.outboxLoadPromise = null
        })
      }
    },
    async loadQueuedMessages(ownerUserId = this.draftOwnerUserId, loadVersion = this.outboxLoadVersion) {
      if (ownerUserId === this.draftOwnerUserId && loadVersion === this.outboxLoadVersion) {
        this.outboxLoaded = false
        this.outboxLoadFailed = false
      }
      try {
        const entries = await loadChatOutbox(ownerUserId)
        if (ownerUserId !== this.draftOwnerUserId || loadVersion !== this.outboxLoadVersion) return
        this.queuedMessages = groupedOutbox(entries.map((entry) => ({
          ...entry,
          attachments: (entry.attachments || []).map((attachment) => ({
            ...attachment,
            file: markRaw(attachment.file)
          }))
        })))
        this.queuedSendPausedSessions = Object.fromEntries(
          Object.entries(this.queuedMessages)
            .filter(([, queue]) => queue[0]?.paused)
            .map(([key]) => [key, true])
        )
        this.outboxLoaded = true
        this.scheduleQueueDrain(0)
      } catch (error) {
        if (ownerUserId === this.draftOwnerUserId && loadVersion === this.outboxLoadVersion) {
          this.outboxLoadFailed = true
        }
      }
    },
    async fetchDefaults() {
      this.sessionDefaults = sanitizedSessionOptions({
        ...defaultSessionOptions(),
        ...(await api.get('/sessions/defaults')).data
      })
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
      const limits = useInstanceLimitsStore()
      const incoming = Array.from(files || [])
      if (this.activeAttachmentDraft.length + incoming.length > limits.maxAttachmentsPerMessage) {
        return { added: 0, error: `Du kannst höchstens ${limits.maxAttachmentsPerMessage} Dateien pro Nachricht anhängen.` }
      }
      for (const file of incoming) {
        if (!Number.isFinite(file?.size) || file.size <= 0) {
          return { added: 0, error: 'Die ausgewählte Datei ist leer.' }
        }
        if (file.size > limits.maxUploadBytes) {
          return { added: 0, error: `Eine Datei darf höchstens ${limits.maxUploadMib} MiB groß sein.` }
        }
      }
      const incomingBytes = incoming.reduce((total, file) => total + file.size, 0)
      const messageBytes = this.activeAttachmentDraft.reduce(
        (total, attachment) => total + (Number(attachment.file?.size) || 0),
        0
      )
      if (messageBytes + incomingBytes > limits.maxMessageUploadBytes) {
        return {
          added: 0,
          error: `Die Anhänge einer Nachricht dürfen zusammen höchstens ${limits.maxMessageUploadMib} MiB groß sein.`
        }
      }
      const draftBytes = [
        ...Object.values(this.attachmentDrafts.newSessions).flat(),
        ...Object.values(this.attachmentDrafts.sessionMessages).flat(),
        ...Object.values(this.queuedMessages).flat().flatMap((entry) => entry.attachments)
      ].reduce((total, attachment) => total + (Number(attachment.file?.size) || 0), 0)
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
      else {
        const target = []
        this.attachmentDrafts.newSession = target
        this.attachmentDrafts.newSessions[sessionScopeKey(this.sessionsProjectId)] = target
      }
    },
    clearAllAttachmentDrafts() {
      const newSessionAttachments = [...new Set([
        ...Object.values(this.attachmentDrafts.newSessions || {}),
        this.attachmentDrafts.newSession
      ])]
      for (const attachment of [
        ...newSessionAttachments.flat(),
        ...Object.values(this.attachmentDrafts.sessionMessages).flat()
      ]) {
        if (attachment.objectUrl) globalThis.URL?.revokeObjectURL?.(attachment.objectUrl)
      }
      this.attachmentDrafts = { newSession: [], sessionMessages: {} }
      this.attachmentDrafts.newSessions = { standalone: this.attachmentDrafts.newSession }
      this.audioDictations = { newSession: null, newSessions: { standalone: null }, sessionMessages: {} }
    },
    clearAllDrafts() {
      const ownerUserId = this.draftOwnerUserId
      this.cancelQueueDrain()
      this.clearAllAttachmentDrafts()
      releaseAttachmentDrafts(
        Object.values(this.queuedMessages).flat().flatMap((entry) => entry.attachments)
      )
      this.queuedMessages = {}
      this.queuedSendPausedSessions = {}
      this.outboxLoaded = !ownerUserId
      this.outboxLoadVersion += 1
      this.drafts = emptyDrafts()
      const scope = sessionScopeKey(this.sessionsProjectId)
      if (scope !== 'standalone') {
        this.drafts.newSessions[scope] = normalizedNewSessionDraft(null)
        this.drafts.newSession = this.drafts.newSessions[scope]
        this.attachmentDrafts.newSessions[scope] = []
        this.attachmentDrafts.newSession = this.attachmentDrafts.newSessions[scope]
        this.audioDictations.newSessions[scope] = null
        this.audioDictations.newSession = null
      }
      if (typeof window !== 'undefined') clearPersistedDrafts()
      return ownerUserId ? clearChatOutbox(ownerUserId) : Promise.resolve()
    },
    attachmentReplacementError(files, sessionId, sourceSessionId = sessionId) {
      const limits = useInstanceLimitsStore()
      if (files.length > limits.maxAttachmentsPerMessage) {
        return `Du kannst höchstens ${limits.maxAttachmentsPerMessage} Dateien pro Nachricht anhängen.`
      }
      for (const file of files) {
        if (!Number.isFinite(file?.size) || file.size <= 0) {
          return 'Die wiederhergestellte Datei ist leer.'
        }
        if (file.size > limits.maxUploadBytes) {
          return `Eine Datei darf höchstens ${limits.maxUploadMib} MiB groß sein.`
        }
      }
      const incomingBytes = files.reduce((total, file) => total + file.size, 0)
      if (incomingBytes > limits.maxMessageUploadBytes) {
        return `Die Anhänge einer Nachricht dürfen zusammen höchstens ${limits.maxMessageUploadMib} MiB groß sein.`
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
        ...Object.values(this.attachmentDrafts.newSessions).flat(),
        ...Object.values(this.attachmentDrafts.sessionMessages).flat(),
        ...Object.values(this.queuedMessages).flat().flatMap((entry) => entry.attachments)
      ].reduce((total, attachment) => total + (Number(attachment.file?.size) || 0), 0)
      const replacedBytes = replaced.reduce(
        (total, attachment) => total + (Number(attachment.file?.size) || 0),
        0
      )
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
      else {
        this.attachmentDrafts.newSession = target
        this.attachmentDrafts.newSessions[sessionScopeKey(this.sessionsProjectId)] = target
      }
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
        this.audioDictations.newSessions[sessionScopeKey(this.sessionsProjectId)] = value
      }
    },
    resetNewDraft(projectId = this.sessionsProjectId) {
      const scope = sessionScopeKey(projectId)
      for (const attachment of this.attachmentDrafts.newSessions[scope] || []) {
        if (attachment.objectUrl) globalThis.URL?.revokeObjectURL?.(attachment.objectUrl)
      }
      const draft = {
        message: '',
        options: sanitizedSessionOptions(this.sessionDefaults)
      }
      this.drafts.newSessions[scope] = draft
      this.attachmentDrafts.newSessions[scope] = []
      this.audioDictations.newSessions[scope] = null
      if (scope === sessionScopeKey(this.sessionsProjectId)) {
        this.drafts.newSession = draft
        this.attachmentDrafts.newSession = this.attachmentDrafts.newSessions[scope]
        this.audioDictations.newSession = null
      }
      this.persistDrafts()
    },
    async setSessionScope(projectId = null) {
      const normalizedProjectId = projectId || null
      const targetList = this.ensureSessionList(normalizedProjectId)
      if (this.sessionsProjectId === normalizedProjectId && targetList.loaded) return
      if (
        this.sessionsProjectId !== normalizedProjectId
        && !this.current
        && this.selectedSessionId === null
      ) {
        this.sessionRequestController?.abort()
        this.sessionLoadVersion += 1
        if (this.running && !this.runId) {
          this.running = false
          this.stopping = false
          this.cancelRequested = false
          this.clearActivity()
        }
      }
      const previousScope = sessionScopeKey(this.sessionsProjectId)
      this.drafts.newSessions[previousScope] = this.drafts.newSession
      this.attachmentDrafts.newSessions[previousScope] = this.attachmentDrafts.newSession
      this.audioDictations.newSessions[previousScope] = this.audioDictations.newSession
      this.sessionsProjectId = normalizedProjectId
      const scope = sessionScopeKey(normalizedProjectId)
      if (!this.drafts.newSessions[scope]) {
        this.drafts.newSessions[scope] = {
          message: '',
          options: sanitizedSessionOptions(this.sessionDefaults)
        }
      }
      this.drafts.newSession = this.drafts.newSessions[scope]
      if (!this.attachmentDrafts.newSessions[scope]) this.attachmentDrafts.newSessions[scope] = []
      this.attachmentDrafts.newSession = this.attachmentDrafts.newSessions[scope]
      if (!Object.hasOwn(this.audioDictations.newSessions, scope)) {
        this.audioDictations.newSessions[scope] = null
      }
      this.audioDictations.newSession = this.audioDictations.newSessions[scope]
      this.persistDrafts()
      if (!targetList.loaded) await this.fetchSessions(null, normalizedProjectId)
    },
    async fetchSessions(config = null, projectId = this.sessionsProjectId) {
      const list = this.ensureSessionList(projectId)
      if (list.loadRequest) return list.loadRequest
      const version = ++list.version
      list.loading = true
      list.loadFailed = false
      const request = (async () => {
        const response = await api.get('/sessions', {
          ...(config || {}),
          params: {
            ...(config?.params || {}),
            ...sessionScopeParams(projectId),
            pageSize: SESSION_PAGE_SIZE
          }
        })
        if (version !== list.version) return
        list.items = sortSessions(response.data.items)
        list.nextCursor = response.data.nextCursor
        list.loaded = true
        list.loadMoreFailed = false
      })()
      list.loadRequest = markRaw(request)
      try {
        await request
      } catch (error) {
        if (version === list.version) list.loadFailed = true
        throw error
      } finally {
        if (list.loadRequest === request) {
          list.loadRequest = null
          list.loading = false
        }
      }
    },
    async refreshSessions(config = null, projectId = this.sessionsProjectId) {
      const list = this.ensureSessionList(projectId)
      if (!list.loaded) return this.fetchSessions(config, projectId)
      const version = ++list.version
      const targetCount = Math.max(SESSION_PAGE_SIZE, list.items.length)
      const sessions = []
      let cursor = null
      do {
        const pageSize = Math.min(100, targetCount - sessions.length)
        const response = await api.get('/sessions', {
          ...(config || {}),
          params: {
            ...(config?.params || {}),
            ...sessionScopeParams(projectId),
            pageSize,
            cursor: cursor || undefined
          }
        })
        if (version !== list.version) return
        sessions.push(...response.data.items)
        cursor = response.data.nextCursor
      } while (cursor && sessions.length < targetCount)
      list.items = sortSessions([...new Map(sessions.map((session) => [session.id, session])).values()])
      list.nextCursor = cursor
      list.loadMoreFailed = false
    },
    async fetchMoreSessions(config = null, projectId = this.sessionsProjectId) {
      const list = this.ensureSessionList(projectId)
      const cursor = list.nextCursor
      if (!cursor) return
      if (list.pageRequest) return list.pageRequest
      const version = ++list.version
      list.loadingMore = true
      list.loadMoreFailed = false
      const request = (async () => {
        const response = await api.get('/sessions', {
          ...(config || {}),
          params: {
            ...(config?.params || {}),
            ...sessionScopeParams(projectId),
            pageSize: SESSION_PAGE_SIZE,
            cursor
          }
        })
        if (version !== list.version || cursor !== list.nextCursor) return
        const sessions = new Map(list.items.map((session) => [session.id, session]))
        for (const session of response.data.items) sessions.set(session.id, session)
        list.items = sortSessions([...sessions.values()])
        list.nextCursor = response.data.nextCursor
      })()
      list.pageRequest = markRaw(request)
      try {
        await request
      } catch (error) {
        if (version === list.version) list.loadMoreFailed = true
        throw error
      } finally {
        if (list.pageRequest === request) list.pageRequest = null
        list.loadingMore = false
      }
    },
    async fetchAllSessions(config = null, projectId = this.sessionsProjectId) {
      const list = this.ensureSessionList(projectId)
      if (!list.loaded) await this.fetchSessions(config, projectId)
      while (list.nextCursor) await this.fetchMoreSessions(config, projectId)
    },
    async fetchSessionCatalog(config = null) {
      const version = ++this.sessionCatalogVersion
      const sessions = []
      let cursor = null
      do {
        const response = await api.get('/sessions', {
          ...(config || {}),
          params: { ...(config?.params || {}), pageSize: 100, cursor: cursor || undefined }
        })
        sessions.push(...response.data.items)
        cursor = response.data.nextCursor
      } while (cursor)
      if (version !== this.sessionCatalogVersion) return this.sessionCatalog
      this.sessionCatalog = sortSessions(sessions)
      this.pruneOrphanedSessionState()
      return this.sessionCatalog
    },
    retrySessionPagination(projectId = this.sessionsProjectId) {
      this.ensureSessionList(projectId).loadMoreFailed = false
    },
    pruneOrphanedSessionState() {
      const sessionIds = new Set(this.sessionCatalog.map((session) => session.id))
      for (const id of Object.keys(this.drafts.sessionMessages)) {
        if (!sessionIds.has(id)) delete this.drafts.sessionMessages[id]
      }
      for (const id of Object.keys(this.attachmentDrafts.sessionMessages)) {
        if (!sessionIds.has(id)) this.clearAttachmentDraft(id)
      }
      for (const id of Object.keys(this.audioDictations.sessionMessages)) {
        if (!sessionIds.has(id)) delete this.audioDictations.sessionMessages[id]
      }
      for (const id of Object.keys(this.queuedMessages)) {
        if (!id.startsWith('new:') && id !== this.current?.id && !sessionIds.has(id)) {
          void this.clearQueuedMessages(id)
        }
      }
      this.persistDrafts()
    },
    async refreshGeneratedTitle(sessionId) {
      for (const delay of [1000, 2000, 4000, 8000, 15000, 30000]) {
        await new Promise((resolve) => setTimeout(resolve, delay))
        const localSession = this.current?.id === sessionId
          ? this.current
          : this.findCachedSession(sessionId)
        if (!ACTIVE_TITLE_GENERATION_STATUSES.has(localSession?.titleGenerationStatus)) return
        try {
          await this.refreshSessions(null, localSession?.projectId || null)
        } catch {
          continue
        }
        const session = this.findCachedSession(sessionId)
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
      this.scheduleQueueDrain(0)
      const loadVersion = this.sessionLoadVersion
      const controller = id ? markRaw(new AbortController()) : null
      this.sessionRequestController = controller
      this.current = null
      this.olderMessagesLoading = false
      this.olderMessagesLoadFailed = false
      this.olderMessagesPromise = null
      this.loadError = null
      const controlCenter = useControlCenterStore()
      if (id) {
        controlCenter.bindSession(this.findCachedSession(id) || { id })
      } else {
        controlCenter.resetDraft()
      }
      if (this.runSessionId !== id || (!id && this.running && !this.runId)) {
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
        this.processOutputs = {}
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
        controlCenter.syncSession(session)
        this.current = session
        void this.markSessionRead(session).catch(() => {})
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
      const refresh = (this.sessionRefreshPromise || Promise.resolve()).catch(() => {}).then(async () => {
        if (this.selectedSessionId !== id || this.loading) return
        this.sessionRequestController?.abort()
        this.sessionLoadVersion += 1
        const loadVersion = this.sessionLoadVersion
        const controller = markRaw(new AbortController())
        this.sessionRequestController = controller
        try {
          const session = (await api.get(`/sessions/${id}`, { signal: controller.signal })).data
          if (this.selectedSessionId === id && this.sessionLoadVersion === loadVersion) {
            if (this.current?.id === id) {
              session.messages = mergeMessages(this.current.messages, session.messages)
              session.contextSummaries = mergeMessages(
                this.current.contextSummaries,
                session.contextSummaries
              )
              session.nextMessageCursor = this.current.nextMessageCursor
            }
            this.current = session
            void this.markSessionRead(session).catch(() => {})
            this.loadError = null
            if (session.activeRun && this.runId !== session.activeRun.id) {
              this.watchRun(id, session.activeRun.id, session.activeRun)
            } else if (!session.activeRun && !this.running) {
              this.clearActivity()
            }
            return session
          }
        } catch (error) {
          if (!controller.signal.aborted) throw error
        } finally {
          if (this.sessionRequestController === controller) this.sessionRequestController = null
        }
      })
      this.sessionRefreshPromise = markRaw(refresh)
      try {
        return await refresh
      } finally {
        if (this.sessionRefreshPromise === refresh) this.sessionRefreshPromise = null
      }
    },
    async loadOlderMessages(id = this.current?.id) {
      const session = this.current
      const cursor = session?.id === id ? session.nextMessageCursor : null
      if (this.olderMessagesPromise) return await this.olderMessagesPromise
      if (!id || !cursor) return []
      this.olderMessagesLoading = true
      this.olderMessagesLoadFailed = false
      const request = (async () => {
        const { data } = await api.get(`/sessions/${id}/messages`, {
          params: { cursor, pageSize: SESSION_PAGE_SIZE }
        })
        if (this.current?.id !== id || this.current.nextMessageCursor !== cursor) return []
        const existingIds = new Set((this.current.messages || []).map((message) => message.id))
        const added = (data.items || []).filter((message) => !existingIds.has(message.id))
        const existingSummaryIds = new Set(
          (this.current.contextSummaries || []).map((summary) => summary.id)
        )
        const addedSummaries = (data.contextSummaries || [])
          .filter((summary) => !existingSummaryIds.has(summary.id))
        this.current.messages = [...added, ...(this.current.messages || [])]
        this.current.contextSummaries = [
          ...addedSummaries,
          ...(this.current.contextSummaries || [])
        ]
        this.current.nextMessageCursor = data.nextCursor || null
        return added
      })()
      this.olderMessagesPromise = markRaw(request)
      try {
        return await request
      } catch (error) {
        if (this.current?.id === id) this.olderMessagesLoadFailed = true
        throw error
      } finally {
        if (this.current?.id === id) this.olderMessagesLoading = false
        if (this.olderMessagesPromise === request) this.olderMessagesPromise = null
      }
    },
    async loadThroughMessage(messageId) {
      while (
        this.current
        && !this.current.messages?.some((message) => message.id === messageId)
        && this.current.nextMessageCursor
      ) {
        await this.loadOlderMessages(this.current.id)
      }
      return Boolean(this.current?.messages?.some((message) => message.id === messageId))
    },
    async updateActive(patch) {
      if (this.current) {
        await this.updateCurrent(patch)
        return
      }
      const options = this.drafts.newSession.options
      const fields = {
        model_id: 'modelId',
        reasoning_effort: 'reasoningEffort',
        memory_mode: 'memoryMode',
        web_search_policy: 'webSearchPolicy',
        preferred_execution_agent_id: 'preferredExecutionAgentId',
        current_directory: 'currentDirectory'
      }
      const normalizedPatch = { ...patch }
      if (
        Object.hasOwn(patch, 'preferred_execution_agent_id')
        && patch.preferred_execution_agent_id !== options.preferredExecutionAgentId
      ) {
        if (!Object.hasOwn(patch, 'current_directory')) normalizedPatch.current_directory = null
      }
      for (const [field, value] of Object.entries(normalizedPatch)) {
        if (fields[field]) options[fields[field]] = value
        if (field === 'memory_mode') {
          options.effectiveMemoryMode = options.memoryUnavailableReason
            && options.memoryUnavailableReason !== 'memory.modelToolsUnavailable'
            ? 'disabled'
            : value
        }
      }
      Object.assign(options, sanitizedSessionOptions(options))
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
      const normalizedPatch = { ...patch }
      if (
        Object.hasOwn(patch, 'preferred_execution_agent_id')
        && patch.preferred_execution_agent_id !== session.preferredExecutionAgentId
      ) {
        if (!Object.hasOwn(patch, 'current_directory')) normalizedPatch.current_directory = null
      }
      this.sessionSettingsPending += 1
      try {
        const { data } = await api.patch(`/sessions/${session.id}`, normalizedPatch)
        if (this.current?.id === session.id && this.selectedSessionId === session.id) {
          this.current = { ...session, ...data }
        }
        this.updateCachedSession(data.id, (item) => ({ ...item, ...data }))
        return data
      } finally {
        this.sessionSettingsPending = Math.max(0, this.sessionSettingsPending - 1)
      }
    },
    async renameSession(id, title) {
      const { data } = await api.patch(`/sessions/${id}`, { title })
      this.updateCachedSession(id, (session) => ({ ...session, ...data }))
      if (this.current?.id === id) this.current = { ...this.current, ...data }
    },
    async moveSession(id, projectId) {
      const catalogHadSession = this.sessionCatalog.some((session) => session.id === id)
      const { data } = await api.patch(`/sessions/${id}`, { project_id: projectId || null })
      const queued = this.queuedMessages[id] || []
      for (const entry of queued) {
        entry.projectId = projectId || null
        if (entry.activeFile?.root !== 'session') continue
        entry.activeFile = projectId && entry.activeFile.root_identity === id
          ? { ...entry.activeFile, root_identity: projectId }
          : null
      }
      if (queued.length) {
        try {
          await saveChatOutboxEntries(queued.map((entry) => toRaw(entry)))
        } catch {
          for (const entry of queued) entry.paused = true
          this.queuedSendPausedSessions[id] = true
          notifyError({}, 'Die vorgemerkten Nachrichten konnten lokal nicht aktualisiert werden.')
        }
      }
      this.removeCachedSession(id)
      const targetList = this.sessionLists[sessionScopeKey(projectId)]
      if (targetList?.loadRequest) {
        try {
          await targetList.loadRequest
        } catch {
          // The destination remains retryable when its independent list request failed.
        }
      }
      if (targetList?.loaded) {
        targetList.version += 1
        targetList.items = sortSessions([data, ...targetList.items])
      }
      if (catalogHadSession) {
        this.sessionCatalogVersion += 1
        this.sessionCatalog = sortSessions([data, ...this.sessionCatalog])
      }
      if (this.current?.id === id) this.current = { ...this.current, ...data }
      return data
    },
    async setSessionPinned(id, pinned) {
      const { data } = await api.patch(`/sessions/${id}`, { pinned })
      this.updateCachedSession(id, (session) => ({ ...session, ...data }))
      if (this.current?.id === id) this.current = { ...this.current, ...data }
    },
    async deleteSession(id, { automationMode = null, automationImpact = null, suppressErrorToast = false } = {}) {
      const config = {
        ...(suppressErrorToast ? { suppressErrorToast: true } : {}),
        ...(automationMode ? { params: { automationMode, automationImpact } } : {})
      }
      const queued = [...(this.queuedMessages[id] || [])]
      const ownerUserId = this.draftOwnerUserId
      const loadVersion = this.outboxLoadVersion
      if (queued.length) {
        try {
          await deleteChatOutboxEntries(queued.map((entry) => entry.id))
        } catch (error) {
          notifyError({}, 'Vorgemerkte Nachrichten konnten nicht gelöscht werden.')
          throw error
        }
      }
      try {
        if (Object.keys(config).length) await api.delete(`/sessions/${id}`, config)
        else await api.delete(`/sessions/${id}`)
      } catch (error) {
        if (
          queued.length
          && ownerUserId === this.draftOwnerUserId
          && loadVersion === this.outboxLoadVersion
        ) {
          try {
            await saveChatOutboxEntries(queued.map((entry) => toRaw(entry)))
          } catch {
            notifyError({}, 'Vorgemerkte Nachrichten konnten nicht wiederhergestellt werden.')
          }
        }
        throw error
      }
      releaseAttachmentDrafts(queued.flatMap((entry) => entry.attachments))
      delete this.queuedMessages[id]
      delete this.queuedSendPausedSessions[id]
      this.removeCachedSession(id)
      delete this.drafts.sessionMessages[id]
      this.clearAttachmentDraft(id)
      this.setAudioDictation(null, id)
      this.persistDrafts()
      if (this.current?.id === id) this.current = null
    },
    async sessionDeleteImpact(id, { suppressErrorToast = false } = {}) {
      const { data } = await api.get(
        `/sessions/${id}/delete-impact`,
        suppressErrorToast ? { suppressErrorToast: true } : undefined
      )
      return data
    },
    clearRunTracking() {
      const sessionId = this.runSessionId
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
      this.processOutputs = {}
      this.clearActivity()
      if (sessionId) this.setSessionRunStatus(sessionId, null)
    },
    setSessionRunStatus(sessionId, status) {
      this.updateCachedSession(sessionId, (session) => ({ ...session, activeRunStatus: status }))
    },
    updateSessionSummary(session) {
      const summary = { ...session }
      delete summary.messages
      delete summary.contextSummaries
      delete summary.activeRun
      const list = this.sessionLists[sessionScopeKey(summary.projectId)]
      if (!list?.loaded) return
      const existing = list.items.some((item) => item.id === session.id)
      list.version += 1
      list.items = sortSessions(existing
        ? list.items.map((item) => item.id === session.id ? { ...item, ...summary } : item)
        : [...list.items, summary])
    },
    applySessionResult(session) {
      this.current = session
      this.updateSessionSummary(session)
    },
    async markSessionRead(session) {
      if (!session?.hasUnreadMessages) return
      const message = lastAssistantMessage(session.messages)
      if (!message) return
      await api.post(
        `/sessions/${session.id}/read`,
        { throughMessageId: message.id },
        { suppressErrorToast: true }
      )
      if (this.current?.id === session.id) this.current.hasUnreadMessages = false
      this.updateCachedSession(session.id, (summary) => ({ ...summary, hasUnreadMessages: false }))
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
        const deletesSession = !session.nextMessageCursor && session.messages?.[0]?.id === message.id
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
          try {
            await this.clearQueuedMessages(session.id)
          } catch {
            notifyError({}, 'Vorgemerkte Nachrichten konnten nicht gelöscht werden.')
          }
          this.removeCachedSession(session.id)
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
        void useQuestionStore().fetchPending().catch(() => {})
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
        void useQuestionStore().fetchPending().catch(() => {})
        return data
      } finally {
        this.historyActionPending = false
      }
    },
    async send(content) {
      if (this.outboxSaving || this.historyActionPending || this.sessionSettingsPending) return null
      this.outboxSaving = true
      const submitImmediately = !this.running
        && !this.stopping
        && !this.runId
        && !this.activeQueuedMessages.length
        && !this.queueDrainPromise
        && !this.messageQueueActive
      if (submitImmediately) {
        this.running = true
        this.setActivity('submitting', { startedAt: new Date().toISOString() })
      }
      if (this.outboxLoadPromise) await this.outboxLoadPromise
      let entry
      try {
        entry = await this.queueMessage(content)
      } finally {
        this.outboxSaving = false
      }
      if (!entry) {
        if (submitImmediately) {
          this.running = false
          this.stopping = false
          this.cancelRequested = false
          this.clearActivity()
        }
        return null
      }
      if (submitImmediately) {
        this.running = false
      }
      return this.drainOutbox()
    },
    async queueMessage(content) {
      const ownerUserId = this.draftOwnerUserId
      const loadVersion = this.outboxLoadVersion
      if (!ownerUserId) return null
      const text = String(content || '').trim()
      const attachments = [...this.activeAttachmentDraft]
      if (!text && !attachments.length) return null
      const sessionId = this.current?.id || null
      const entry = {
        id: newSubmissionId(),
        ownerUserId,
        sessionId,
        projectId: this.current?.projectId || this.sessionsProjectId || null,
        content: text,
        attachments: attachments.map((attachment) => ({
          id: attachment.id,
          file: attachment.file,
          name: attachment.file.name || 'datei',
          kind: attachment.kind,
          mimeType: attachment.mimeType
        })),
        options: sessionId ? null : sanitizedSessionOptions({ ...this.drafts.newSession.options }),
        controlCenter: sessionId ? null : controlCenterRequestState(useControlCenterStore()),
        activeFile: useWorkspaceStore().activeFileSnapshot(),
        queuedAt: new Date().toISOString(),
        paused: false
      }
      try {
        await saveChatOutboxEntry(toRaw(entry))
      } catch (error) {
        notifyError({}, 'Die Nachricht konnte nicht auf diesem Gerät vorgemerkt werden.')
        return null
      }
      if (ownerUserId !== this.draftOwnerUserId || loadVersion !== this.outboxLoadVersion) {
        await deleteChatOutboxEntry(entry.id).catch(() => {})
        return null
      }
      const key = queueKeyForEntry(entry)
      if (!this.queuedMessages[key]) this.queuedMessages[key] = []
      this.queuedMessages[key].push(entry)
      this.clearAttachmentDraft(sessionId)
      if (sessionId) delete this.drafts.sessionMessages[sessionId]
      else this.drafts.newSession.message = ''
      this.persistDrafts()
      return entry
    },
    async removeQueuedMessage(id, key = this.activeQueueKey) {
      if (id === this.queueSendingEntryId) return null
      const queue = key ? this.queuedMessages[key] : null
      const index = queue ? queue.findIndex((entry) => entry.id === id) : -1
      if (index < 0) return null
      const entry = queue[index]
      try {
        await deleteChatOutboxEntry(entry.id)
      } catch {
        notifyError({}, 'Die vorgemerkte Nachricht konnte nicht entfernt werden.')
        return null
      }
      queue.splice(index, 1)
      releaseAttachmentDrafts(entry.attachments)
      if (!queue.length) {
        delete this.queuedMessages[key]
        delete this.queuedSendPausedSessions[key]
      } else if (queue[0].paused) {
        this.queuedSendPausedSessions[key] = true
      } else {
        delete this.queuedSendPausedSessions[key]
      }
      return entry
    },
    async restoreQueuedMessage(id, key = this.activeQueueKey) {
      const entry = await this.removeQueuedMessage(id, key)
      if (!entry) return null
      const sessionId = entry.sessionId || null
      if (sessionId) this.drafts.sessionMessages[sessionId] = entry.content
      else this.drafts.newSession.message = entry.content
      this.replaceAttachmentFiles(entry.attachments.map((attachment) => attachment.file), sessionId)
      this.setAudioDictation(null, sessionId)
      this.persistDrafts()
      return entry
    },
    async clearQueuedMessages(key = this.activeQueueKey) {
      const entries = this.queuedMessages[key] || []
      if (!entries.length) return
      await deleteChatOutboxEntries(entries.map((entry) => entry.id))
      releaseAttachmentDrafts(entries.flatMap((entry) => entry.attachments))
      delete this.queuedMessages[key]
      delete this.queuedSendPausedSessions[key]
    },
    async resumeQueuedSend(key = this.activeQueueKey) {
      if (!key) return
      const entry = this.queuedMessages[key]?.[0]
      if (entry?.paused) {
        entry.paused = false
        try {
          await saveChatOutboxEntry(toRaw(entry))
        } catch {
          entry.paused = true
          notifyError({}, 'Der Versand konnte nicht wieder aufgenommen werden.')
          return
        }
      }
      delete this.queuedSendPausedSessions[key]
      this.scheduleQueueDrain(0)
    },
    cancelQueueDrain() {
      if (this.queueDrainTimer !== null) clearTimeout(this.queueDrainTimer)
      this.queueDrainTimer = null
    },
    scheduleQueueDrain(delay = OUTBOX_RETRY_MS) {
      if (this.queueDrainTimer !== null) return
      this.queueDrainTimer = setTimeout(() => {
        this.queueDrainTimer = null
        void this.drainOutbox()
      }, delay)
    },
    async drainOutbox() {
      if (!this.outboxLoaded) return null
      if (this.queueDrainPromise) return this.queueDrainPromise
      const request = this.drainNextOutboxEntry()
      this.queueDrainPromise = markRaw(request)
      try {
        return await request
      } finally {
        if (this.queueDrainPromise === request) this.queueDrainPromise = null
        if (
          this.queuedSendReady
          || Object.values(this.queuedMessages).some((queue) => (
            queue[0]?.sessionId && queue[0].sessionId !== this.selectedSessionId
              && !this.queuedSendPausedSessions[queueKeyForEntry(queue[0])]
          ))
        ) this.scheduleQueueDrain(0)
      }
    },
    async drainNextOutboxEntry() {
      this.cancelQueueDrain()
      let entry = this.queuedSendReady ? this.activeQueuedMessages[0] : null
      if (!entry) {
        const backgroundEntries = Object.values(this.queuedMessages)
          .filter((queue) => queue[0]?.sessionId && queue[0].sessionId !== this.selectedSessionId)
          .map((queue) => queue[0])
          .filter((item) => !this.queuedSendPausedSessions[queueKeyForEntry(item)])
          .sort((left, right) => left.queuedAt.localeCompare(right.queuedAt))
        for (const candidate of backgroundEntries) {
          try {
            const { data } = await api.get(`/sessions/${candidate.sessionId}`, {
              suppressErrorToast: true
            })
            if (!data.activeRun) {
              entry = candidate
              break
            }
          } catch (error) {
            const key = queueKeyForEntry(candidate)
            candidate.paused = true
            try {
              await saveChatOutboxEntry(toRaw(candidate))
            } catch {
              notifyError({}, 'Der Pausenstatus konnte nicht gespeichert werden.')
            }
            this.queuedSendPausedSessions[key] = true
            notifyError(error)
          }
        }
      }
      if (!entry) {
        if (Object.values(this.queuedMessages).some((queue) => (
          queue[0]?.sessionId && !this.queuedSendPausedSessions[queueKeyForEntry(queue[0])]
        ))) this.scheduleQueueDrain()
        return null
      }
      return this.submitOutboxEntry(entry)
    },
    async submitOutboxEntry(entry) {
      const key = queueKeyForEntry(entry)
      this.queueSendingEntryId = entry.id
      try {
        let result
        if (!entry.sessionId) {
          result = await this.startSession(entry.content, entry.attachments, {
            options: entry.options,
            projectId: entry.projectId,
            controlCenter: entry.controlCenter,
            activeFile: entry.activeFile,
            runId: entry.id,
            suppressErrorToast: true
          })
          if (result?.created) this.createdSessionResult = result
        } else if (this.current?.id === entry.sessionId && this.selectedSessionId === entry.sessionId) {
          result = await this.sendToCurrent(entry.content, entry.attachments, {
            clearDraft: false,
            activeFile: entry.activeFile,
            runId: entry.id,
            suppressErrorToast: true
          })
        } else {
          const payload = {
            content: entry.content,
            run_id: entry.id,
            ...(entry.activeFile ? { active_file: entry.activeFile } : {})
          }
          const config = { suppressErrorToast: true }
          await (entry.attachments.length
            ? api.post(
                `/sessions/${entry.sessionId}/messages/with-attachments`,
                buildAttachmentFormData(payload, entry.attachments),
                config
              )
            : api.post(`/sessions/${entry.sessionId}/messages`, payload, config))
          void this.refreshSessions(null, entry.projectId).catch(() => {})
          result = { created: false, sessionId: entry.sessionId }
        }
        await deleteChatOutboxEntry(entry.id)
        const queue = this.queuedMessages[key] || []
        this.queuedMessages[key] = queue.filter((item) => item.id !== entry.id)
        if (!this.queuedMessages[key].length) delete this.queuedMessages[key]
        if (this.queuedMessages[key]?.[0]?.paused) this.queuedSendPausedSessions[key] = true
        else delete this.queuedSendPausedSessions[key]
        releaseAttachmentDrafts(entry.attachments)
        return result
      } catch (error) {
        const stillQueued = this.queuedMessages[key]?.some((item) => item.id === entry.id)
        if (!stillQueued) return null
        if (errorDescriptor(error)?.code === 'session.runAlreadyActive') {
          this.scheduleQueueDrain()
        } else {
          entry.paused = true
          try {
            await saveChatOutboxEntry(toRaw(entry))
          } catch {
            notifyError({}, 'Der Pausenstatus konnte nicht gespeichert werden.')
          }
          this.queuedSendPausedSessions[key] = true
          notifyError(error)
        }
        return null
      } finally {
        if (this.queueSendingEntryId === entry.id) this.queueSendingEntryId = null
      }
    },
    async stop() {
      if (!this.running || this.stopping) return
      this.cancelRequested = true
      this.running = false
      this.stopping = false
      void this.cancelActiveRun()
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
          this.running = true
        }
      }
    },
    async startSession(
      content,
      attachments = [],
      {
        options: requestedOptions = null,
        projectId: requestedProjectId,
        controlCenter: requestedControlCenter = null,
        activeFile: requestedActiveFile,
        runId = null,
        suppressErrorToast = false
      } = {}
    ) {
      const sessionLoadVersion = this.sessionLoadVersion
      const selectedSessionId = this.selectedSessionId
      const projectId = requestedProjectId === undefined ? this.sessionsProjectId : requestedProjectId
      this.running = !this.cancelRequested
      this.streamContent = ''
      this.streamReasoning = null
      this.streamModelName = null
      this.processOutputs = {}
      this.setActivity('submitting', { startedAt: new Date().toISOString() })
      const options = sanitizedSessionOptions(requestedOptions || this.drafts.newSession.options)
      try {
        const activeFile = requestedActiveFile === undefined
          ? useWorkspaceStore().activeFileSnapshot()
          : requestedActiveFile
        const payload = {
          content,
          ...(runId ? { run_id: runId } : {}),
          project_id: projectId,
          model_id: options.modelId,
          reasoning_effort: options.reasoningEffort,
          memory_mode: options.memoryMode,
          web_search_policy: options.webSearchPolicy,
          preferred_execution_agent_id: options.preferredExecutionAgentId,
          ...(options.preferredExecutionAgentId
            ? { current_directory: options.currentDirectory }
            : {}),
          control_center: requestedControlCenter || controlCenterRequestState(useControlCenterStore()),
          ...(activeFile ? { active_file: activeFile } : {})
        }
        const config = suppressErrorToast ? [{ suppressErrorToast: true }] : []
        const { data } = attachments.length
          ? await api.post('/sessions/with-attachments', buildAttachmentFormData(payload, attachments), ...config)
          : await api.post('/sessions', payload, ...config)
        const userMessage = data.message || { id: `local-${Date.now()}`, role: 'user', content }
        const session = {
          ...data.session,
          userRequestCount: Number.isInteger(data.session.userRequestCount)
            ? Math.max(data.session.userRequestCount, 1)
            : 1,
          messages: [userMessage]
        }
        const wasStillSelected = this.sessionLoadVersion === sessionLoadVersion
          && this.selectedSessionId === selectedSessionId
          && !this.current
        if (wasStillSelected) {
          const controlCenter = useControlCenterStore()
          try {
            const reconciled = await controlCenter.attachCreatedSession(
              session.id,
              session.controlCenter
            )
            if (reconciled) session.controlCenter = reconciled
          } catch {
            if (
              this.sessionLoadVersion === sessionLoadVersion
              && this.selectedSessionId === selectedSessionId
              && !this.current
            ) controlCenter.syncSession(session)
          }
        }
        const stillSelected = wasStillSelected
          && this.sessionLoadVersion === sessionLoadVersion
          && this.selectedSessionId === selectedSessionId
          && !this.current
        const list = this.ensureSessionList(data.session.projectId)
        if (list.loadRequest) {
          try {
            await list.loadRequest
          } catch {
            // The created session remains canonical even if the independent list load failed.
          }
        }
        list.version += 1
        list.items = sortSessions([
          data.session,
          ...list.items.filter((item) => item.id !== session.id)
        ])
        this.resetNewDraft(projectId)
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
    async sendToCurrent(
      content,
      attachments = [],
      { clearDraft = true, activeFile: requestedActiveFile, runId = null, suppressErrorToast = false } = {}
    ) {
      const session = this.current
      const sessionLoadVersion = this.sessionLoadVersion
      this.running = !this.cancelRequested
      this.streamContent = ''
      this.streamReasoning = null
      this.streamModelName = null
      this.processOutputs = {}
      this.setActivity('submitting', { startedAt: new Date().toISOString() })
      try {
        const activeFile = requestedActiveFile === undefined
          ? useWorkspaceStore().activeFileSnapshot()
          : requestedActiveFile
        const payload = {
          content,
          ...(runId ? { run_id: runId } : {}),
          ...(activeFile ? { active_file: activeFile } : {})
        }
        const config = suppressErrorToast ? [{ suppressErrorToast: true }] : []
        const { data } = attachments.length
          ? await api.post(
              `/sessions/${session.id}/messages/with-attachments`,
              buildAttachmentFormData(payload, attachments),
              ...config
            )
          : await api.post(`/sessions/${session.id}/messages`, payload, ...config)
        if (data.message) {
          const canonicalIndex = session.messages.findIndex((message) => message.id === data.message.id)
          if (canonicalIndex >= 0) {
            session.messages[canonicalIndex] = data.message
          } else {
            session.messages.push(data.message)
            if (data.message.role === 'user' && Number.isInteger(session.userRequestCount)) {
              session.userRequestCount += 1
            }
          }
          if (data.message.createdAt) {
            session.lastMessageAt = data.message.createdAt
            this.updateCachedSession(session.id, (item) => ({
              ...item,
              lastMessageAt: data.message.createdAt
            }))
          }
        }
        if (clearDraft) {
          this.clearAttachmentDraft(session.id)
          delete this.drafts.sessionMessages[session.id]
          this.persistDrafts()
        }
        if (
          this.sessionLoadVersion === sessionLoadVersion
          && this.selectedSessionId === session.id
          && this.current?.id === session.id
        ) {
          this.watchRun(session.id, data.runId, { status: 'queued', modelName: data.modelName })
          await this.cancelActiveRun()
        } else {
          void this.refreshSessions().catch(() => {})
        }
        return { created: false, sessionId: session.id }
      } catch (error) {
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
      const toolViewContexts = new Map()
      let finished = false
      this.eventSource = markRaw(source)
      this.runSessionId = sessionId
      this.runId = runId
      this.running = !this.cancelRequested
      if (run.status) this.setSessionRunStatus(sessionId, run.status)
      this.streamModelName = run.modelName || this.streamModelName
      source.onopen = () => {
        if (this.eventSource !== source) return
        this.reconnectAttempts = 0
        if (this.activity.phase === 'reconnecting') {
          const connectedPhase = run.status === 'waiting_approval' || run.status === 'waiting_input'
            ? run.status
            : run.status === 'queued' ? 'queued' : 'preparing_context'
          this.setActivity(connectedPhase, { startedAt: run.createdAt })
        }
      }
      if (run.reconnecting) {
        this.setActivity('reconnecting', { startedAt: run.createdAt })
      } else {
        const resumedPhase = run.status === 'waiting_approval' || run.status === 'waiting_input'
          ? run.status
          : run.status === 'queued' ? 'queued' : 'preparing_context'
        this.setActivity(resumedPhase, { startedAt: run.createdAt })
      }
      source.addEventListener('run.started', (event) => {
        if (this.eventSource !== source) return
        const payload = JSON.parse(event.data)
        this.streamModelName = payload.modelName || this.streamModelName
        this.setSessionRunStatus(sessionId, 'running')
        this.setActivity('preparing_context', { startedAt: payload.createdAt })
      })
      source.addEventListener('run.phase', (event) => {
        if (this.eventSource !== source) return
        const payload = JSON.parse(event.data)
        if (payload.phase === 'processing_tool' && payload.toolCallId && !toolViewContexts.has(payload.toolCallId)) {
          const workspace = useWorkspaceStore()
          const controlCenter = useControlCenterStore()
          toolViewContexts.set(payload.toolCallId, {
            viewVersion: workspace.viewVersion,
            editorDirty: workspace.editorDirty,
            controlCenterOpen: controlCenter.open,
            activeContentId: controlCenter.activeContentId
          })
        }
        this.setActivity(payload.phase, payload)
      })
      source.addEventListener('session.title.updated', (event) => {
        if (this.eventSource !== source) return
        const payload = JSON.parse(event.data)
        if (this.current?.id === payload.sessionId) {
          this.current.title = payload.title
          this.current.titleGenerationStatus = 'completed'
        }
        this.updateCachedSession(payload.sessionId, (session) => ({
          ...session,
          title: payload.title,
          titleGenerationStatus: 'completed'
        }))
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
      source.addEventListener('context.compacted', () => {
        if (this.eventSource !== source) return
        void this.refreshSession(sessionId).catch(() => {})
      })
      source.addEventListener('process.output', (event) => {
        if (this.eventSource !== source) return
        const payload = JSON.parse(event.data)
        const toolCallId = payload.toolCallId
        if (!toolCallId) return
        const incoming = payload.output && typeof payload.output === 'object'
          ? payload.output
          : payload
        const previous = this.processOutputs[toolCallId] || { stdout: '', stderr: '' }
        const next = { ...previous }
        if (typeof incoming.stdout === 'string') next.stdout = incoming.stdout
        if (typeof incoming.stderr === 'string') next.stderr = incoming.stderr
        const stream = incoming.stream || incoming.event
        const content = incoming.content ?? incoming.data
        if (['stdout', 'stderr'].includes(stream) && typeof content === 'string') {
          next[stream] = content
        }
        if (typeof incoming.truncated === 'boolean') next.truncated = incoming.truncated
        if (typeof incoming.outputTruncated === 'boolean') next.truncated = incoming.outputTruncated
        this.processOutputs = { ...this.processOutputs, [toolCallId]: next }
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
      source.addEventListener('tool.completed', (event) => {
        if (this.eventSource !== source) return
        const payload = JSON.parse(event.data)
        const refresh = this.refreshSession(sessionId)
        void refresh?.then(async (session) => {
          const start = toolViewContexts.get(payload.toolCallId)
          toolViewContexts.delete(payload.toolCallId)
          if (!session || this.current?.id !== sessionId) return
          const result = session.messages?.find((message) => (
            message.role === 'tool' && message.toolCallId === payload.toolCallId
          ))
          const workspace = useWorkspaceStore()
          const fileAction = toolFileActions(result)[0]
          const attachmentAction = result?.attachments
            ?.map((attachment) => managedSessionFileTarget(attachment, sessionId))
            .find(Boolean)
          const action = fileAction || attachmentAction
          const toolCall = session.messages
            ?.flatMap((message) => message.toolCalls || [])
            .find((item) => item.id === payload.toolCallId)
          const argumentsValue = toolCall?.arguments || {}
          const sourceRoot = argumentsValue.source_root || argumentsValue.root
          const managedRootMutation = FILESYSTEM_MUTATION_TOOLS.has(payload.tool) && [
            argumentsValue.root,
            argumentsValue.source_root,
            argumentsValue.destination_root
          ].some((root) => ['files', 'session'].includes(root))
          if (
            !payload.metadata?.errorCode
            && (managedRootMutation || attachmentAction?.root === 'session')
          ) void workspace.refreshStorageUsage(sessionId)
          if (!start) return
          const sourcePath = argumentsValue.source_path || argumentsValue.path
          const sourceMatchesOpenFile = ['delete_path', 'move_path'].includes(payload.tool)
            && workspace.viewedEntry?.path
            && (workspace.viewedEntry.path === sourcePath
              || workspace.viewedEntry.path.startsWith(`${sourcePath}/`))
            && (sourceRoot !== 'files' || workspace.rootIdentity === (session.projectId || sessionId))
            && !workspace.toolContext
            && ((sourceRoot === 'files' && workspace.scope === 'session')
              || (sourceRoot === 'computer' && workspace.scope === 'filesystem'))
          if (sourceMatchesOpenFile) workspace.noteFileMutation(workspace.viewedEntry.path)
          if (!action) return
          const sameOpenFile = workspace.viewedEntry?.path === action.path
            && (action.root !== 'session' || workspace.rootIdentity === action.rootIdentity)
            && !workspace.toolContext
            && ((action.root === 'session' && workspace.scope === 'session')
              || (action.root === 'workspace' && workspace.scope === 'filesystem'))
          if (sameOpenFile) {
            workspace.noteFileMutation(action.path)
            return
          }
          const shouldOpen = fileAction || attachmentAction.fileRevision === 1
          if (!shouldOpen) return
          const controlCenter = useControlCenterStore()
          if (
            start.editorDirty
            || workspace.editorDirty
            || workspace.viewVersion !== start.viewVersion
            || controlCenter.open !== start.controlCenterOpen
            || controlCenter.activeContentId !== start.activeContentId
          ) return
          workspace.configure({
            sessionId,
            rootIdentity: session.projectId || sessionId,
            currentDirectory: session.currentDirectory,
            workspaceContext: useExecutionAgentStore().workspaceContextFor(this.activeContext)
          })
          await workspace.openToolFile(action)
          if (
            workspace.viewedEntry?.path !== action.path
            || (action.root === 'session' && workspace.rootIdentity !== action.rootIdentity)
            || controlCenter.open !== start.controlCenterOpen
            || controlCenter.activeContentId !== start.activeContentId
          ) return
          await controlCenter.openApp('files')
        }).catch(() => {})
        if (FILESYSTEM_MUTATION_TOOLS.has(payload.tool) && !payload.metadata?.errorCode) {
          const workspace = useWorkspaceStore()
          if (workspace.sessionId === sessionId) void workspace.refresh()
        }
      })
      source.addEventListener('approval.created', (event) => {
        if (this.eventSource !== source) return
        const approval = JSON.parse(event.data).approval
        this.setActivity('waiting_approval', {
          tool: approval.details?.tool,
          toolCallId: approval.details?.toolCallId
        })
        this.setSessionRunStatus(sessionId, 'waiting_approval')
        useApprovalStore().add(approval)
      })
      source.addEventListener('approval.resolved', () => {
        if (this.eventSource !== source) return
        this.setActivity('processing_tool', {
          tool: this.activity.tool,
          toolCallId: this.activity.toolCallId
        })
        this.setSessionRunStatus(sessionId, 'running')
        useApprovalStore().fetchPending()
      })
      source.addEventListener('approval.expired', () => {
        if (this.eventSource !== source) return
        this.setActivity('processing_tool', {
          tool: this.activity.tool,
          toolCallId: this.activity.toolCallId
        })
        this.setSessionRunStatus(sessionId, 'running')
        useApprovalStore().fetchPending()
      })
      source.addEventListener('question.created', (event) => {
        if (this.eventSource !== source) return
        const question = JSON.parse(event.data).question
        this.setActivity('waiting_input', {
          tool: 'question',
          toolCallId: question.toolCallId
        })
        this.setSessionRunStatus(sessionId, 'waiting_input')
        useQuestionStore().add(question)
      })
      for (const name of ['question.answered', 'question.rejected', 'question.expired']) {
        source.addEventListener(name, () => {
          if (this.eventSource !== source) return
          this.setActivity('processing_tool', { tool: 'question', toolCallId: this.activity.toolCallId })
          this.setSessionRunStatus(sessionId, 'running')
          void useQuestionStore().fetchPending().catch(() => {})
        })
      }
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
        this.setSessionRunStatus(sessionId, null)
        if (!this.historyActionPending) {
          void this.refreshSession(sessionId)
            .catch(() => {})
            .finally(() => {
              this.cancelQueueDrain()
              this.scheduleQueueDrain(0)
            })
          void this.refreshSessions().catch(() => {})
        } else {
          this.scheduleQueueDrain(0)
        }
        useApprovalStore().fetchPending()
        void useQuestionStore().fetchPending().catch(() => {})
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
        this.running = !this.cancelRequested
        if (!this.cancelRequested) this.setActivity('reconnecting')
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
          this.setSessionRunStatus(sessionId, null)
          void this.refreshSession(sessionId).catch(() => {})
          this.refreshSessions()
          useApprovalStore().fetchPending()
          void useQuestionStore().fetchPending().catch(() => {})
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
          if (['queued', 'running', 'waiting_approval', 'waiting_input'].includes(data.status)) {
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

export {
  buildAttachmentFormData,
  downloadMessageAttachmentFiles,
  MAX_ATTACHMENT_BYTES,
  MAX_ATTACHMENT_DRAFT_BYTES,
  MAX_ATTACHMENT_DRAFTS
} from 'src/features/chat/attachments'
