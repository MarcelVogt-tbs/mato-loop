import 'fake-indexeddb/auto'
import { createPinia, setActivePinia } from 'pinia'
import { watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn()
  }
}))

vi.mock('src/boot/axios', () => ({ api }))
vi.mock('src/utils/notifications', () => ({ notifyError: vi.fn() }))

import { useApprovalStore } from '../src/stores/approvals'
import { useChatStore, MAX_ATTACHMENT_DRAFT_BYTES } from '../src/stores/chat'
import { useControlCenterStore } from '../src/stores/control-center'
import { useQuestionStore } from '../src/stores/questions'
import { useWorkspaceStore } from '../src/stores/workspace'
import { clearChatOutbox, loadChatOutbox } from '../src/utils/chat-outbox'
import { buildAttachmentFormData } from '../src/features/chat/attachments'
import { withQueuedTranscriptMessages } from '../src/utils/chat-transcript'

const originalURL = globalThis.URL

function createStorage() {
  const values = new Map()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key)
  }
}

class EventSourceMock {
  constructor(url) {
    this.url = url
    this.listeners = new Map()
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener)
  }

  close() {}
}

function activeSession(chat, id = 'session-1') {
  chat.current = { id, messages: [] }
  chat.selectedSessionId = id
}

function attachment(name = 'datei.txt', size = 1) {
  const file = new Blob(['x'.repeat(Math.min(size, 10))], { type: 'text/plain' })
  Object.defineProperty(file, 'name', { value: name })
  Object.defineProperty(file, 'size', { value: size })
  return { id: `attachment-${name}`, file, kind: 'file', mimeType: 'text/plain', objectUrl: 'blob:preview' }
}

describe('chat message queue', () => {
  beforeEach(async () => {
    await clearChatOutbox('user-1')
    global.window = { localStorage: createStorage() }
    global.EventSource = EventSourceMock
    global.URL = { createObjectURL: vi.fn(() => 'blob:preview'), revokeObjectURL: vi.fn() }
    setActivePinia(createPinia())
    const chat = useChatStore()
    chat.setDraftOwner('user-1')
    await chat.outboxLoadPromise
    chat.cancelQueueDrain()
    vi.clearAllMocks()
  })

  afterEach(() => {
    useChatStore().cancelQueueDrain()
    delete global.window
    delete global.EventSource
    global.URL = originalURL
  })

  it('persists text and attachments while a run is active', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.setDraft('Später senden')
    chat.attachmentDrafts.sessionMessages['session-1'] = [attachment()]

    const entry = await chat.queueMessage('Später senden')

    expect(entry.content).toBe('Später senden')
    expect(chat.activeQueuedMessages).toHaveLength(1)
    expect(chat.activeQueuedMessages[0].attachments).toHaveLength(1)
    expect(chat.activeDraft).toBe('')
    expect(chat.activeAttachmentDraft).toEqual([])
    expect(global.URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview')
    expect(chat.queuedSendReady).toBe(false)
  })

  it('queues a prepared answer without consuming the current draft or attachments', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.setDraft('Mein angefangener Text')
    chat.attachmentDrafts.sessionMessages['session-1'] = [attachment()]

    await chat.send('Verwende ERP-Kunde 123 – Acme.', { preserveDraft: true })

    expect(chat.activeQueuedMessages[0].content).toBe('Verwende ERP-Kunde 123 – Acme.')
    expect(chat.activeQueuedMessages[0].attachments).toEqual([])
    expect(chat.activeDraft).toBe('Mein angefangener Text')
    expect(chat.activeAttachmentDraft).toHaveLength(1)
    expect(global.URL.revokeObjectURL).not.toHaveBeenCalled()
  })

  it('preserves voice markers through IndexedDB, failed sends, retry and restore', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    const audio = new File(['wav'], 'voice.wav', { type: 'audio/wav' })
    chat.addAttachmentFiles([audio])
    chat.addAttachmentFiles([audio], { voiceMessage: true })
    chat.addAttachmentFiles([audio], { voiceMessage: true })
    const entry = await chat.queueMessage('')
    expect((await loadChatOutbox('user-1'))[0].attachments.map((item) => item.voiceMessage))
      .toEqual([false, true, true])

    setActivePinia(createPinia())
    const restored = useChatStore()
    restored.setDraftOwner('user-1')
    await restored.outboxLoadPromise
    restored.cancelQueueDrain()
    activeSession(restored)
    api.post.mockRejectedValueOnce({ response: { status: 422 } })
    await restored.drainOutbox()
    expect(JSON.parse(api.post.mock.calls.at(-1)[1].get('payload')).voiceAttachmentIndices).toEqual([1, 2])
    expect(restored.activeQueuedMessages[0].attachments[1].voiceMessage).toBe(true)

    restored.setAudioDictation({ files: [audio] }, 'session-1')
    await restored.restoreQueuedMessage(entry.id)
    expect(restored.getAudioDictation('session-1').files).toHaveLength(1)
    expect(restored.activeAttachmentDraft.map((item) => item.voiceMessage)).toEqual([false, true, true])
    expect(restored.activeAttachmentDraft.every((item) => item.objectUrl === 'blob:preview')).toBe(true)
    const form = buildAttachmentFormData({ voiceAttachmentIndices: [0] }, restored.activeAttachmentDraft.slice(1))
    expect(JSON.parse(form.get('payload')).voiceAttachmentIndices).toEqual([0, 1])
    vi.spyOn(restored, 'watchRun').mockImplementation(() => {})
    api.post.mockResolvedValueOnce({ data: { message: { id: 'sent' }, runId: 'run-1' } })
    await restored.send('')
    expect(JSON.parse(api.post.mock.calls.at(-1)[1].get('payload')).voiceAttachmentIndices).toEqual([1, 2])
    expect(restored.activeQueuedMessages).toEqual([])
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview')
  })

  it('retains rejected recordings when sending the first message creates a session', async () => {
    const chat = useChatStore()
    const audio = new File(['wav'], 'rejected.wav', { type: 'audio/wav' })
    chat.setAudioDictation({ files: [audio] })
    vi.spyOn(chat, 'watchRun').mockImplementation(() => {})
    vi.spyOn(useControlCenterStore(), 'attachCreatedSession').mockResolvedValue(null)
    api.post.mockResolvedValue({
      data: { session: { id: 'session-1' }, message: { id: 'sent' }, runId: 'run-1' }
    })
    await chat.startSession('Text')
    expect(chat.getAudioDictation('session-1').files[0]).toBe(audio)
    expect(chat.getAudioDictation()).toBeNull()
  })

  it('keeps an idle submission queued until the server accepts it', async () => {
    let resolveSend
    api.post.mockReturnValue(new Promise((resolve) => { resolveSend = resolve }))
    const chat = useChatStore()
    activeSession(chat)
    vi.spyOn(chat, 'watchRun').mockImplementation(() => {})
    const displayed = []
    const stopWatching = watch(() => withQueuedTranscriptMessages(
      chat.current.messages, chat.activeQueuedMessages, chat.queueSendingEntryId
    ), (messages) => displayed.push(messages.filter((message) => message.content === 'Jetzt senden').length))

    const sending = chat.send('Jetzt senden')

    await vi.waitFor(() => expect(api.post).toHaveBeenCalledOnce())
    expect(chat.activeQueuedMessages.map((entry) => entry.content)).toEqual(['Jetzt senden'])
    expect(chat.queueSendingEntryId).toBe(chat.activeQueuedMessages[0].id)
    expect(chat.current.messages).toEqual([])

    resolveSend({
      data: {
        runId: 'run-1',
        message: { id: 'message-1', role: 'user', content: 'Jetzt senden' }
      }
    })
    await sending
    stopWatching()

    expect(chat.activeQueuedMessages).toEqual([])
    expect(displayed.every((count) => count === 1)).toBe(true)
    expect(chat.current.messages).toEqual([
      { id: 'message-1', role: 'user', content: 'Jetzt senden' }
    ])
  })

  it('persists the first queued message with a reactive session profile', async () => {
    const chat = useChatStore()
    const profile = { agentModels: { plan: { modelId: 'plan-model', reasoningEffort: 'high' } } }
    chat.drafts.newSession.options.sessionProfile = profile
    await chat.queueMessage('Test')
    expect((await loadChatOutbox('user-1'))[0]?.options.sessionProfile).toEqual(profile)
  })

  it('restores the outbox with its stable id and file after a reload', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.attachmentDrafts.sessionMessages['session-1'] = [attachment('beleg.txt')]
    const queued = await chat.queueMessage('Bleibt erhalten')

    setActivePinia(createPinia())
    const restored = useChatStore()
    restored.setDraftOwner('user-1')
    await restored.outboxLoadPromise
    restored.cancelQueueDrain()
    activeSession(restored)

    expect(restored.activeQueuedMessages).toHaveLength(1)
    expect(restored.activeQueuedMessages[0]).toMatchObject({
      id: queued.id,
      content: 'Bleibt erhalten',
      sessionId: 'session-1'
    })
    expect(restored.activeQueuedMessages[0].attachments[0]).toMatchObject({
      name: 'beleg.txt',
      file: expect.any(Blob)
    })
  })

  it('keeps each queued profile through reload and retries while a later selection remains pending', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.current.agentProfileId = 'agent-a'
    chat.running = true
    await chat.updateActive({ agent_profile_id: 'agent-b' })
    const first = await chat.queueMessage('Erste')
    await chat.updateActive({ agent_profile_id: 'agent-a' })
    const second = await chat.queueMessage('Zweite')
    await chat.updateActive({ agent_profile_id: 'agent-b' })

    setActivePinia(createPinia())
    const restored = useChatStore()
    restored.setDraftOwner('user-1')
    await restored.outboxLoadPromise
    restored.cancelQueueDrain()
    activeSession(restored)
    restored.current.agentProfileId = 'agent-a'
    vi.spyOn(restored, 'watchRun').mockImplementation(() => {})
    expect(restored.activeQueuedMessages.map((entry) => entry.agentProfileId)).toEqual(['agent-b', 'agent-a'])
    expect(restored.activeAgentProfileId).toBe('agent-b')

    api.post.mockRejectedValueOnce({ response: { status: 409 } })
    await restored.drainOutbox()
    expect(restored.activeQueuedMessages[0].paused).toBe(true)
    expect(api.post.mock.calls[0][1].agent_profile_id).toBe('agent-b')
    restored.cancelQueueDrain()
    api.post.mockResolvedValueOnce({ data: {
      message: { id: 'message-1' }, runId: first.id, agentProfileId: 'agent-b'
    } })
    await restored.resumeQueuedSend()
    await vi.waitFor(() => expect(restored.activeQueuedMessages).toHaveLength(1))
    restored.cancelQueueDrain()
    expect(restored.current.agentProfileId).toBe('agent-b')
    expect(restored.activeAgentProfileId).toBe('agent-b')
    expect(restored.activeQueuedMessages[0].id).toBe(second.id)

    restored.running = false
    restored.runId = null
    api.post.mockResolvedValueOnce({ data: {
      message: { id: 'message-2' }, runId: second.id, agentProfileId: 'agent-a'
    } })
    await restored.drainOutbox()
    expect(api.post.mock.calls.at(-1)[1].agent_profile_id).toBe('agent-a')
    expect(restored.current.agentProfileId).toBe('agent-a')
    expect(restored.activeAgentProfileId).toBe('agent-b')
  })

  it('sends the queued profile when the session is no longer selected', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.current.agentProfileId = 'agent-a'
    chat.running = true
    await chat.updateActive({ agent_profile_id: 'agent-b' })
    const entry = await chat.queueMessage('Im Hintergrund')
    chat.current = { id: 'session-2', messages: [] }
    chat.selectedSessionId = 'session-2'
    vi.spyOn(chat, 'refreshSessions').mockResolvedValue([])
    api.get.mockResolvedValue({ data: { activeRun: null } })
    api.post.mockResolvedValue({ data: {
      message: { id: 'message-1' }, runId: entry.id, agentProfileId: 'agent-b'
    } })

    await chat.drainOutbox()

    expect(api.post).toHaveBeenCalledWith('/sessions/session-1/messages', {
      content: 'Im Hintergrund', run_id: entry.id, agent_profile_id: 'agent-b'
    }, { suppressErrorToast: true })
    expect(chat.drafts.sessionAgentProfiles['session-1']).toBeUndefined()
  })

  it('queues the first message again immediately after undoing it', async () => {
    const chat = useChatStore()
    const message = { id: 'message-1', role: 'user', content: 'Noch einmal', attachments: [] }
    chat.current = {
      id: 'session-1',
      messages: [message],
      workspaceContext: { connected: true, operations: ['filesystem.list'] }
    }
    chat.selectedSessionId = 'session-1'
    api.get.mockResolvedValue({ data: { id: 'session-1', messages: [], activeRun: null } })
    api.post.mockResolvedValue({
      data: { draft: { content: message.content } }
    })

    await chat.undoUserMessage(message)
    const queued = await chat.queueMessage(chat.activeDraft)

    expect(queued).not.toBeNull()
    expect(queued.sessionId).toBe('session-1')

    setActivePinia(createPinia())
    const restored = useChatStore()
    restored.setDraftOwner('user-1')
    await restored.outboxLoadPromise
    restored.cancelQueueDrain()
    activeSession(restored)

    expect(restored.activeQueuedMessages).toHaveLength(1)
    expect(restored.activeQueuedMessages[0]).toMatchObject({
      id: queued.id,
      content: 'Noch einmal',
      sessionId: 'session-1'
    })
  })

  it('persists the queue-time active file snapshot and sends it unchanged', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    const controlCenter = useControlCenterStore()
    controlCenter.sessionId = 'session-1'
    controlCenter.open = true
    controlCenter.tabs = [{ appId: 'files', pinned: false }]
    controlCenter.activeAppId = 'files'
    const workspace = useWorkspaceStore()
    workspace.sessionId = 'session-1'
    workspace.scope = 'session'
    workspace.rootIdentity = 'session-1'
    workspace.openEntry({ kind: 'file', name: 'plan.md', path: 'plan.md' })
    workspace.filePreviews['plan.md'] = { revision: 'a'.repeat(64) }

    const entry = await chat.queueMessage('Mit Datei')
    workspace.filePreviews['plan.md'].revision = 'b'.repeat(64)
    chat.running = false
    vi.spyOn(chat, 'watchRun').mockImplementation(() => {})
    api.post.mockResolvedValue({ data: { message: { id: 'message-1' }, runId: 'run-1' } })
    await chat.drainOutbox()

    const snapshot = {
      root: 'session',
      root_identity: 'session-1',
      path: 'plan.md',
      sha256: 'a'.repeat(64),
      dirty: false
    }
    expect(entry.activeFile).toEqual(snapshot)
    expect(api.post).toHaveBeenCalledWith('/sessions/session-1/messages', {
      content: 'Mit Datei',
      run_id: entry.id,
      active_file: snapshot
    }, { suppressErrorToast: true })
  })

  it('does not queue a sibling session file with the current chat message', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    const controlCenter = useControlCenterStore()
    controlCenter.sessionId = 'session-1'
    controlCenter.open = true
    controlCenter.tabs = [{ appId: 'files', pinned: false }]
    controlCenter.activeAppId = 'files'
    const workspace = useWorkspaceStore()
    workspace.sessionId = 'session-1'
    workspace.scope = 'session'
    workspace.rootIdentity = 'session-2'
    workspace.openEntry({ kind: 'file', name: 'plan.md', path: 'plan.md' })
    workspace.filePreviews['plan.md'] = { revision: 'a'.repeat(64) }

    const entry = await chat.queueMessage('Ohne fremde Datei')

    expect(entry.activeFile).toBeNull()
    expect((await loadChatOutbox('user-1'))[0].activeFile).toBeNull()
  })

  it('waits for pending approvals and questions before sending', async () => {
    const chat = useChatStore()
    const approvals = useApprovalStore()
    const questions = useQuestionStore()
    activeSession(chat)

    approvals.pending = [{ id: 'approval-1', sessionId: 'session-1' }]
    await chat.send('Nachricht')

    expect(api.post).not.toHaveBeenCalled()
    expect(chat.activeQueuedMessages.map((entry) => entry.content)).toEqual(['Nachricht'])
    expect(chat.activity.phase).toBe('idle')
    expect(chat.queuedSendReady).toBe(false)

    approvals.pending = []
    questions.pending = [{ id: 'question-1', sessionId: 'session-1' }]
    expect(chat.queuedSendReady).toBe(false)

    questions.pending = []
    expect(chat.messageQueueActive).toBe(true)
    expect(chat.queuedSendReady).toBe(true)
  })

  it('drains messages in FIFO order and keeps a newer draft', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    const first = await chat.queueMessage('Zuerst')
    const second = await chat.queueMessage('Danach')
    chat.running = false
    chat.setDraft('Neuer Entwurf')
    vi.spyOn(chat, 'watchRun').mockImplementation(() => {})
    api.post.mockResolvedValue({ data: { message: { id: 'message-1' }, runId: 'run-1' } })

    await chat.drainOutbox()
    chat.running = false
    await chat.drainOutbox()

    expect(api.post.mock.calls.map(([, payload]) => payload)).toEqual([
      { content: 'Zuerst', run_id: first.id },
      { content: 'Danach', run_id: second.id }
    ])
    expect(chat.activeQueuedMessages).toEqual([])
    expect(chat.activeDraft).toBe('Neuer Entwurf')
  })

  it('waits for the completed run refresh before sending the next message', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    const first = await chat.queueMessage('Zuerst')
    const second = await chat.queueMessage('Danach')
    chat.running = false
    api.post
      .mockResolvedValueOnce({
        data: {
          message: { id: 'message-1', role: 'user', content: 'Zuerst' },
          runId: 'run-1'
        }
      })
      .mockResolvedValueOnce({
        data: {
          message: { id: 'message-2', role: 'user', content: 'Danach' },
          runId: 'run-2'
        }
      })

    await chat.drainOutbox()
    let resolveRefresh
    api.get.mockImplementation((url) => {
      if (url === '/sessions/session-1') {
        return new Promise((resolve) => { resolveRefresh = resolve })
      }
      return Promise.resolve({ data: url === '/sessions' ? { items: [], nextCursor: null } : [] })
    })

    chat.eventSource.listeners.get('run.completed')()
    await vi.waitFor(() => expect(resolveRefresh).toBeTypeOf('function'))
    await chat.drainOutbox()

    expect(api.post).toHaveBeenCalledTimes(1)
    expect(chat.queuedSendReady).toBe(false)

    resolveRefresh({
      data: {
        id: 'session-1',
        messages: [
          { id: 'message-1', role: 'user', content: 'Zuerst' },
          { id: 'answer-1', role: 'assistant', content: 'Erste Antwort' }
        ],
        activeRun: null
      }
    })

    await vi.waitFor(() => expect(api.post).toHaveBeenCalledTimes(2))
    expect(api.post.mock.calls.map(([, payload]) => payload)).toEqual([
      { content: 'Zuerst', run_id: first.id },
      { content: 'Danach', run_id: second.id }
    ])
    expect(chat.current.messages.map((message) => message.content)).toEqual([
      'Zuerst',
      'Erste Antwort',
      'Danach'
    ])
  })

  it('saves a direct message before adding it to the active run, ahead of queued messages', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.watchRun('session-1', 'run-1', { status: 'running' })
    await chat.queueMessage('Später')
    api.post.mockImplementation(async (_url, payload) => {
      expect((await loadChatOutbox('user-1')).map((entry) => entry.content)).toEqual([
        'Jetzt', 'Später'
      ])
      return { data: { message: { id: payload.message_id, role: 'user', content: 'Jetzt', continuationRunId: 'run-1' }, runId: 'run-1' } }
    })

    await chat.send('Jetzt', { interrupt: true })

    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.post.mock.calls[0][0]).toBe('/sessions/session-1/runs/run-1/messages')
    expect(api.post.mock.calls[0][1]).toMatchObject({ content: 'Jetzt', message_id: expect.any(String) })
    expect(chat.eventSource.url).toBe('/api/sessions/session-1/runs/run-1/events')
    expect(chat.current.messages.map((message) => message.content)).toEqual(['Jetzt'])
    expect(chat.activeQueuedMessages.map((entry) => entry.content)).toEqual(['Später'])
  })

  it('keeps direct-message priority after a reload and retains the message when appending fails', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.watchRun('session-1', 'run-1', { status: 'running' })
    await chat.queueMessage('Später')
    api.post.mockRejectedValue(new Error('Append failed'))

    await chat.send('Jetzt', { interrupt: true })
    await vi.waitFor(() => expect(chat.running).toBe(true))

    expect(api.post).toHaveBeenCalledTimes(1)
    expect(chat.activeQueuedMessages.map((entry) => entry.content)).toEqual(['Jetzt', 'Später'])
    expect(chat.activeQueuedMessages[0].paused).toBe(true)
    expect(chat.activeQueuedMessages[0].continuationRunId).toBe('run-1')
    setActivePinia(createPinia())
    const restored = useChatStore()
    restored.setDraftOwner('user-1')
    await restored.outboxLoadPromise
    restored.cancelQueueDrain()
    activeSession(restored)
    expect(restored.activeQueuedMessages.map((entry) => entry.content)).toEqual(['Jetzt', 'Später'])
    api.post.mockResolvedValueOnce({ data: {
      message: { id: restored.activeQueuedMessages[0].id, role: 'user', content: 'Jetzt', continuationRunId: 'run-1' },
      runId: 'run-1'
    } })
    await restored.resumeQueuedSend()
    await vi.waitFor(() => expect(restored.activeQueuedMessages.map((entry) => entry.content)).toEqual(['Später']))
    expect(api.post.mock.calls[1][0]).toBe('/sessions/session-1/runs/run-1/messages')
    restored.cancelQueueDrain()
  })

  it('places a direct message before a partial answer that arrived while it was submitting', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.watchRun('session-1', 'run-1', { status: 'running' })
    let resolveSend
    api.post.mockReturnValue(new Promise((resolve) => { resolveSend = resolve }))
    const sending = chat.send('Korrektur', { interrupt: true })
    await vi.waitFor(() => expect(api.post).toHaveBeenCalledOnce())
    chat.current.messages.push({
      id: 'partial', role: 'assistant', runId: 'run-1', continuationSegment: true,
      content: 'Zwischenstand', createdAt: '2026-01-01T00:00:02Z'
    })
    resolveSend({ data: {
      message: { id: api.post.mock.calls[0][1].message_id, role: 'user',
        content: 'Korrektur', createdAt: '2026-01-01T00:00:01Z', continuationRunId: 'run-1' },
      runId: 'run-1'
    } })
    await sending
    expect(chat.current.messages.map((message) => message.content)).toEqual(['Korrektur', 'Zwischenstand'])
  })

  it('does not cancel a run when the direct message cannot be saved', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.watchRun('session-1', 'run-1', { status: 'running' })
    chat.setDraft('Nicht verlieren')
    vi.stubGlobal('indexedDB', undefined)
    try {
      await chat.send('Nicht verlieren', { interrupt: true })

      expect(api.post).not.toHaveBeenCalled()
      expect(chat.running).toBe(true)
      expect(chat.activeDraft).toBe('Nicht verlieren')
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('keeps an unaccepted direct message when its run has already finished', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.watchRun('session-1', 'run-1', { status: 'running' })
    api.post.mockRejectedValueOnce({ response: { status: 409, data: { error: { code: 'session.runInactive' } } } })

    await chat.send('Noch wichtig', { interrupt: true })

    chat.cancelQueueDrain()
    const pending = chat.activeQueuedMessages[0]
    expect(pending.id).toBeTruthy()
    expect(pending.continuationRunId).toBeUndefined()
    expect((await loadChatOutbox('user-1'))[0].id).toBe(pending.id)
    chat.clearRunTracking()
    chat.running = false
    api.post.mockResolvedValueOnce({ data: {
      message: { id: 'next-message', role: 'user', content: 'Noch wichtig' }, runId: 'run-2'
    } })

    await chat.drainOutbox()

    expect(api.post.mock.calls.map(([url]) => url)).toEqual([
      '/sessions/session-1/runs/run-1/messages', '/sessions/session-1/messages'
    ])
    expect(api.post.mock.calls[1][1].run_id).toBe(pending.id)
  })

  it.each(['run.completed', 'run.failed', 'run.cancelled', 'reconnect', 'navigation', 'session-switch'])(
    'keeps canonical outputs closed until the user opens them after %s', async (terminal) => {
      const chat = useChatStore()
      const sessionId = '550e8400-e29b-41d4-a716-446655440000'
      const runId = '550e8400-e29b-41d4-a716-446655440001'
      activeSession(chat, sessionId)
      api.get.mockResolvedValue({ data: [] })
      vi.spyOn(chat, 'refreshSessions').mockResolvedValue()
      const controlCenter = useControlCenterStore()
      const open = vi.spyOn(controlCenter, 'openFile').mockResolvedValue('file-tab')
      const final = {
        id: sessionId, activeRun: null,
        messages: [
          { id: 'old', role: 'tool', runId: 'other-run', attachments: [] },
          { id: 'hidden', role: 'tool', runId, attachments: [], fileActions: [] },
          { id: 'final', role: 'tool', runId, attachments: [{
            id: 'output', kind: 'file', filename: 'final.stl', mimeType: 'model/stl',
            fileRootId: sessionId, fileId: '550e8400-e29b-41d4-a716-446655440002',
            fileRevision: 3, filePath: 'final.stl', sizeBytes: 10
          }] }
        ]
      }
      const attachment = final.messages[2].attachments[0]
      final.messages[0].attachments = [{ ...attachment, filePath: 'previous-run.stl' }]
      final.messages.push({
        id: 'second', role: 'tool', runId,
        attachments: [attachment, { ...attachment, filePath: 'second.stl' }]
      })
      let resolveFinal
      const refresh = vi.spyOn(chat, 'refreshSession').mockResolvedValue({ ...final, messages: [] })
      chat.watchRun(sessionId, runId)
      const source = chat.eventSource
      source.listeners.get('tool.completed')({ data: JSON.stringify({ tool: 'render_openscad', toolCallId: 'tool-1' }) })
      await Promise.resolve()
      expect(open).not.toHaveBeenCalled()
      refresh.mockImplementation(() => new Promise((resolve) => { resolveFinal = resolve }))
      if (terminal === 'reconnect') {
        api.get.mockResolvedValue({ data: { status: 'cancelled' } })
        await source.onerror()
      } else source.listeners.get(terminal.startsWith('run.') ? terminal : 'run.completed')()
      expect(open).not.toHaveBeenCalled()
      if (terminal === 'navigation') useWorkspaceStore().viewVersion += 1
      if (terminal === 'session-switch') activeSession(chat, 'another-session')
      resolveFinal(final)
       for (let tick = 0; tick < 5; tick++) await Promise.resolve()
       expect(open).not.toHaveBeenCalled()
    }
  )

  it('keeps and pauses a message when sending fails', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    await chat.queueMessage('Fehlschlag')
    chat.running = false
    api.post.mockRejectedValue(new Error('offline'))

    await chat.drainOutbox()

    expect(chat.activeQueuedMessages[0].content).toBe('Fehlschlag')
    expect(chat.queuedSendPaused).toBe(true)
    const firstRunId = api.post.mock.calls[0][1].run_id
    vi.spyOn(chat, 'watchRun').mockImplementation(() => {})
    api.post.mockResolvedValue({ data: { message: { id: 'message-1' }, runId: firstRunId } })
    await chat.resumeQueuedSend()
    chat.cancelQueueDrain()
    await chat.drainOutbox()

    expect(api.post.mock.calls[1][1].run_id).toBe(firstRunId)
    expect(chat.activeQueuedMessages).toEqual([])
  })

  it('waits without pausing when the server still has an active run', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    await chat.queueMessage('Zu früh')
    chat.running = false
    api.post.mockRejectedValue({
      response: { status: 409, data: { error: { code: 'session.runAlreadyActive' } } }
    })

    await chat.drainOutbox()

    expect(chat.activeQueuedMessages).toHaveLength(1)
    expect(chat.queuedSendPaused).toBe(false)
  })

  it('restores a failed send as paused after a reload', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    await chat.queueMessage('Nicht automatisch wiederholen')
    chat.running = false
    api.post.mockRejectedValue(new Error('offline'))
    await chat.drainOutbox()

    setActivePinia(createPinia())
    const restored = useChatStore()
    restored.setDraftOwner('user-1')
    await restored.outboxLoadPromise
    restored.cancelQueueDrain()
    activeSession(restored)

    expect(restored.activeQueuedMessages).toHaveLength(1)
    expect(restored.queuedSendPaused).toBe(true)
  })

  it('accepts only one submission while IndexedDB is saving', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true

    const first = chat.send('Nur einmal')
    const second = chat.send('Nicht doppelt')
    await Promise.all([first, second])

    expect(chat.activeQueuedMessages.map((entry) => entry.content)).toEqual(['Nur einmal'])
  })

  it('removes a queued message from memory and IndexedDB', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    const entry = await chat.queueMessage('Verwerfen')

    await chat.removeQueuedMessage(entry.id)

    expect(chat.activeQueuedMessages).toEqual([])
    setActivePinia(createPinia())
    const restored = useChatStore()
    restored.setDraftOwner('user-1')
    await restored.outboxLoadPromise
    restored.cancelQueueDrain()
    activeSession(restored)
    expect(restored.activeQueuedMessages).toEqual([])
  })

  it('restores a queued message with attachments to the composer', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.attachmentDrafts.sessionMessages['session-1'] = [attachment('entwurf.txt')]
    const first = await chat.queueMessage('Bearbeiten')
    await chat.queueMessage('Danach')

    await chat.restoreQueuedMessage(first.id)

    expect(chat.activeDraft).toBe('Bearbeiten')
    expect(chat.activeAttachmentDraft).toHaveLength(1)
    expect(chat.activeAttachmentDraft[0].file.name).toBe('entwurf.txt')
    expect(chat.activeQueuedMessages.map((entry) => entry.content)).toEqual(['Danach'])
    setActivePinia(createPinia())
    const restored = useChatStore()
    restored.setDraftOwner('user-1')
    await restored.outboxLoadPromise
    restored.cancelQueueDrain()
    activeSession(restored)
    expect(restored.activeQueuedMessages.map((entry) => entry.content)).toEqual(['Danach'])
  })

  it('clears the persisted outbox with the owner drafts', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    await chat.queueMessage('Vertraulich')

    await chat.clearAllDrafts()
    setActivePinia(createPinia())
    const restored = useChatStore()
    restored.setDraftOwner('user-1')
    await restored.outboxLoadPromise
    restored.cancelQueueDrain()
    activeSession(restored)

    expect(restored.activeQueuedMessages).toEqual([])
  })

  it('keeps queued messages in memory when persistent deletion fails', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    await chat.queueMessage('Nicht verlieren')
    const indexedDB = globalThis.indexedDB
    globalThis.indexedDB = undefined

    try {
      await expect(chat.clearQueuedMessages('session-1')).rejects.toThrow()
      expect(chat.activeQueuedMessages).toHaveLength(1)
    } finally {
      globalThis.indexedDB = indexedDB
    }
  })

  it.each([false, true])('keeps recovery on an accepted message when local cleanup fails (new session: %s)', async (newSession) => {
    const chat = useChatStore()
    if (!newSession) activeSession(chat)
    vi.spyOn(chat, 'watchRun').mockImplementation(() => {})
    vi.spyOn(useControlCenterStore(), 'attachCreatedSession').mockResolvedValue(null)
    const entry = await chat.queueMessage('Angenommen')
    api.post.mockResolvedValue({ data: {
      session: { id: 'session-1' }, runId: entry.id,
      message: { id: 'accepted', role: 'user', content: entry.content }
    } })
    const indexedDB = globalThis.indexedDB
    globalThis.indexedDB = undefined
    try {
      await chat.submitOutboxEntry(entry)
    } finally {
      globalThis.indexedDB = indexedDB
    }
    const displayed = withQueuedTranscriptMessages(chat.current.messages, chat.activeQueuedMessages, null)
    chat.running = false
    await chat.resumeQueuedSend()
    chat.cancelQueueDrain()
    await chat.drainOutbox()
    expect([displayed, chat.activeQueuedMessages, api.post.mock.calls.length]).toEqual([
      [{ id: 'accepted', role: 'user', content: entry.content, queueRecoveryId: entry.id }], [], 1
    ])
  })

  it('removes project outbox entries before deletion and restores them after failure', async () => {
    const chat = useChatStore()
    chat.current = { id: 'session-project', projectId: 'project-1', messages: [] }
    chat.selectedSessionId = 'session-project'
    chat.running = true
    const projectEntry = await chat.queueMessage('Projekt behalten')
    chat.current = { id: 'session-other', projectId: 'project-2', messages: [] }
    chat.selectedSessionId = 'session-other'
    const otherEntry = await chat.queueMessage('Andere Queue')
    vi.spyOn(chat, 'scheduleQueueDrain').mockImplementation(() => {})

    const snapshot = await chat.prepareProjectOutboxDeletion('project-1', ['session-project'])

    expect(chat.queuedMessages['session-project']).toBeUndefined()
    expect(chat.queuedMessages['session-other']).toHaveLength(1)
    expect((await loadChatOutbox('user-1')).map((entry) => entry.id)).toEqual([otherEntry.id])

    await chat.restoreProjectOutboxDeletion(snapshot)

    expect(chat.queuedMessages['session-project']).toHaveLength(1)
    expect((await loadChatOutbox('user-1')).map((entry) => entry.id).sort())
      .toEqual([projectEntry.id, otherEntry.id].sort())
  })

  it('aborts project outbox preparation without changing memory when persistence fails', async () => {
    const chat = useChatStore()
    chat.current = { id: 'session-project', projectId: 'project-1', messages: [] }
    chat.selectedSessionId = 'session-project'
    chat.running = true
    await chat.queueMessage('Nicht verlieren')
    vi.spyOn(chat, 'scheduleQueueDrain').mockImplementation(() => {})
    const indexedDB = globalThis.indexedDB
    globalThis.indexedDB = undefined

    try {
      await expect(chat.prepareProjectOutboxDeletion('project-1', ['session-project']))
        .rejects.toThrow()
      expect(chat.queuedMessages['session-project']).toHaveLength(1)
    } finally {
      globalThis.indexedDB = indexedDB
    }
  })

  it('aborts project deletion cleanup when the persisted outbox is unavailable', async () => {
    const chat = useChatStore()
    chat.outboxLoaded = false
    chat.outboxLoadFailed = true

    await expect(chat.prepareProjectOutboxDeletion('project-1'))
      .rejects.toThrow('Chat outbox is unavailable')
  })

  it('clears successful project queues locally and reschedules unrelated queues', async () => {
    const chat = useChatStore()
    chat.current = { id: 'session-project', projectId: 'project-1', messages: [] }
    chat.selectedSessionId = 'session-project'
    chat.running = true
    await chat.queueMessage('Projekt löschen')
    chat.current = { id: 'session-other', projectId: 'project-2', messages: [] }
    chat.selectedSessionId = 'session-other'
    const otherEntry = await chat.queueMessage('Andere Queue')
    const scheduleQueueDrain = vi.spyOn(chat, 'scheduleQueueDrain').mockImplementation(() => {})
    await chat.prepareProjectOutboxDeletion('project-1', ['session-project'])
    scheduleQueueDrain.mockClear()

    await chat.removeProjectScope('project-1', ['session-project'])

    expect(chat.queuedMessages['session-project']).toBeUndefined()
    expect(chat.queuedMessages['session-other']).toHaveLength(1)
    expect(scheduleQueueDrain).toHaveBeenCalledWith(0)
    expect((await loadChatOutbox('user-1')).map((entry) => entry.id)).toEqual([otherEntry.id])
  })

  it('restores the persistent queue when session deletion fails', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    await chat.queueMessage('Nachricht behalten')
    api.delete.mockRejectedValue(new Error('offline'))

    await expect(chat.deleteSession('session-1')).rejects.toThrow('offline')
    setActivePinia(createPinia())
    const restored = useChatStore()
    restored.setDraftOwner('user-1')
    await restored.outboxLoadPromise
    restored.cancelQueueDrain()
    activeSession(restored)

    expect(restored.activeQueuedMessages.map((entry) => entry.content)).toEqual(['Nachricht behalten'])
  })

  it('does not restore a failed deletion after the owner was cleared', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    await chat.queueMessage('Nicht wiederherstellen')
    api.delete.mockImplementation(async () => {
      await chat.clearAllDrafts()
      chat.setDraftOwner(null)
      throw new Error('session expired')
    })

    await expect(chat.deleteSession('session-1')).rejects.toThrow('session expired')
    chat.setDraftOwner('user-1')
    await chat.outboxLoadPromise
    chat.cancelQueueDrain()
    activeSession(chat)

    expect(chat.activeQueuedMessages).toEqual([])
  })

  it('does not resurrect an in-flight message deleted with its session', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    await chat.queueMessage('Verloren')
    chat.running = false
    api.post.mockImplementation(async () => {
      await chat.clearQueuedMessages('session-1')
      throw new Error('offline')
    })

    await chat.drainOutbox()

    expect(chat.queuedMessages['session-1']).toBeUndefined()
    expect(chat.queuedSendPausedSessions['session-1']).toBeUndefined()
  })

  it('sends a ready queue in another session in the background', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    const entry = await chat.queueMessage('Im Hintergrund')
    activeSession(chat, 'session-2')
    chat.running = false
    api.get.mockImplementation((url) => Promise.resolve({
      data: url === '/sessions/session-1' ? { activeRun: null } : { items: [], nextCursor: null }
    }))
    api.post.mockResolvedValue({ data: {} })

    await chat.drainOutbox()

    expect(api.post).toHaveBeenCalledWith('/sessions/session-1/messages', {
      content: 'Im Hintergrund',
      run_id: entry.id
    }, { suppressErrorToast: true })
    expect(chat.queuedMessages['session-1']).toBeUndefined()
  })

  it('watches a background send when its session is selected before server acceptance', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    await chat.queueMessage('Korrektur')
    activeSession(chat, 'session-2')
    chat.running = false
    api.get.mockResolvedValue({ data: { activeRun: null } })
    let acceptSend
    api.post.mockImplementation(() => new Promise((resolve) => { acceptSend = resolve }))

    const sending = chat.drainOutbox()
    await vi.waitFor(() => expect(acceptSend).toBeTypeOf('function'))
    activeSession(chat)
    acceptSend({ data: {
      runId: 'follow-up', message: { id: 'correction', role: 'user', content: 'Korrektur' }
    } })
    await sending

    expect(chat.current.messages.map((message) => message.id)).toEqual(['correction'])
    expect(chat.eventSource.url).toBe('/api/sessions/session-1/runs/follow-up/events')
  })

  it('restores a background queue probe failure as paused', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    await chat.queueMessage('Im Hintergrund warten')
    activeSession(chat, 'session-2')
    chat.running = false
    api.get.mockRejectedValue(new Error('offline'))

    await chat.drainOutbox()
    setActivePinia(createPinia())
    const restored = useChatStore()
    restored.setDraftOwner('user-1')
    await restored.outboxLoadPromise
    restored.cancelQueueDrain()
    activeSession(restored)

    expect(restored.activeQueuedMessages).toHaveLength(1)
    expect(restored.queuedSendPaused).toBe(true)
  })

  it('keeps paused state isolated per session', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    await chat.queueMessage('A')
    chat.queuedSendPausedSessions['session-1'] = true
    chat.queuedSendPausedSessions['session-2'] = true

    await chat.resumeQueuedSend()
    chat.cancelQueueDrain()

    expect(chat.queuedSendPausedSessions['session-2']).toBe(true)
    expect(chat.queuedSendPaused).toBe(false)
  })

  it('counts queued attachments against the global draft budget', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.attachmentDrafts.sessionMessages['session-1'] = [
      attachment('gross.bin', MAX_ATTACHMENT_DRAFT_BYTES)
    ]
    await chat.queueMessage('Groß')

    const result = chat.addAttachmentFiles([{ name: 'bild.png', type: 'image/png', size: 1024 }])

    expect(result.added).toBe(0)
    expect(result.error).toContain('256 MiB')
  })
})
