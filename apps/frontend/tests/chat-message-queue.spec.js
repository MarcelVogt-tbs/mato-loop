import { createPinia, setActivePinia } from 'pinia'
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
import { useQuestionStore } from '../src/stores/questions'

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

function activeSession(chat) {
  chat.current = { id: 'session-1', messages: [] }
  chat.selectedSessionId = 'session-1'
}

describe('chat message queue', () => {
  beforeEach(() => {
    global.window = { localStorage: createStorage() }
    global.EventSource = EventSourceMock
    global.URL = { createObjectURL: vi.fn(() => 'blob:preview'), revokeObjectURL: vi.fn() }
    setActivePinia(createPinia())
    useChatStore().setDraftOwner('user-1')
    vi.clearAllMocks()
  })

  afterEach(() => {
    delete global.window
    delete global.EventSource
    global.URL = originalURL
  })

  it('queues instead of sending while a run is active', () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.setDraft('Später senden')
    chat.attachmentDrafts.sessionMessages['session-1'] = [
      { id: 'a1', file: { size: 1 }, objectUrl: 'blob:preview' }
    ]

    expect(chat.messageQueueActive).toBe(true)
    const entry = chat.queueMessage('Später senden')

    expect(entry.content).toBe('Später senden')
    expect(chat.activeQueuedMessages).toHaveLength(1)
    expect(chat.activeQueuedMessages[0].attachments).toHaveLength(1)
    expect(chat.activeDraft).toBe('')
    expect(chat.activeAttachmentDraft).toEqual([])
    expect(global.URL.revokeObjectURL).not.toHaveBeenCalled()
    expect(chat.queuedSendReady).toBe(false)
  })

  it('waits for pending approvals and questions before sending', () => {
    const chat = useChatStore()
    const approvals = useApprovalStore()
    const questions = useQuestionStore()
    activeSession(chat)
    chat.running = true
    chat.queueMessage('Nachricht')
    chat.running = false

    approvals.pending = [{ id: 'approval-1', sessionId: 'session-1' }]
    expect(chat.messageQueueActive).toBe(true)
    expect(chat.queuedSendReady).toBe(false)

    approvals.pending = []
    questions.pending = [{ id: 'question-1', sessionId: 'session-1' }]
    expect(chat.queuedSendReady).toBe(false)

    questions.pending = []
    expect(chat.messageQueueActive).toBe(false)
    expect(chat.queuedSendReady).toBe(true)
  })

  it('sends the queued message and keeps the current draft untouched', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.queueMessage('Vorgemerkt')
    chat.running = false
    chat.setDraft('Neuer Entwurf')
    api.post.mockResolvedValue({
      data: { message: { id: 'message-1', role: 'user', content: 'Vorgemerkt' }, runId: 'run-1' }
    })

    await chat.flushQueuedMessage()

    expect(api.post).toHaveBeenCalledWith('/sessions/session-1/messages', { content: 'Vorgemerkt' })
    expect(chat.activeQueuedMessages).toEqual([])
    expect(chat.activeDraft).toBe('Neuer Entwurf')
    expect(chat.running).toBe(true)
  })

  it('restores and pauses the queue when sending fails', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.queueMessage('Fehlschlag')
    chat.running = false
    api.post.mockRejectedValue(new Error('offline'))

    await chat.flushQueuedMessage()

    expect(chat.activeQueuedMessages).toHaveLength(1)
    expect(chat.activeQueuedMessages[0].content).toBe('Fehlschlag')
    expect(chat.queuedSendPaused).toBe(true)
    expect(chat.queuedSendReady).toBe(false)

    chat.resumeQueuedSend()
    expect(chat.queuedSendReady).toBe(true)
  })

  it('releases attachment previews when a queued message is removed', () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.attachmentDrafts.sessionMessages['session-1'] = [
      { id: 'a1', file: { size: 1 }, objectUrl: 'blob:preview' }
    ]
    const entry = chat.queueMessage('Verwerfen')

    chat.removeQueuedMessage(entry.id)

    expect(global.URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview')
    expect(chat.activeQueuedMessages).toEqual([])
  })

  it('keeps the queue waiting without pausing when the run is still active', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.queueMessage('Zu früh')
    chat.running = false
    api.get.mockResolvedValue({ data: { id: 'session-1', messages: [], activeRun: null } })
    api.post.mockRejectedValue({
      response: { status: 409, data: { error: { code: 'session.runAlreadyActive' } } }
    })

    await chat.flushQueuedMessage()

    expect(chat.activeQueuedMessages).toHaveLength(1)
    expect(chat.queuedSendPaused).toBe(false)
  })

  it('drops an in-flight queued message when its session is deleted meanwhile', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.attachmentDrafts.sessionMessages['session-1'] = [
      { id: 'a1', file: new Blob(['x']), objectUrl: 'blob:preview' }
    ]
    chat.queueMessage('Verloren')
    chat.running = false
    api.post.mockImplementation(() => {
      chat.clearQueuedMessages('session-1')
      return Promise.reject(new Error('offline'))
    })

    await chat.flushQueuedMessage()

    expect(chat.queuedMessages['session-1']).toBeUndefined()
    expect(chat.queuedSendPaused).toBe(false)
    expect(global.URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview')
  })

  it('keeps the paused state per session', () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.queueMessage('A')
    chat.queuedSendPausedSessions['session-1'] = true
    chat.queuedSendPausedSessions['session-2'] = true

    chat.resumeQueuedSend()

    expect(chat.queuedSendPausedSessions['session-2']).toBe(true)
    expect(chat.queuedSendPaused).toBe(false)
  })

  it('sends a queued message in the background after switching sessions', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.queueMessage('Im Hintergrund')
    chat.selectedSessionId = 'session-2'
    api.post.mockResolvedValue({ data: { message: { id: 'm1' } } })
    api.get.mockResolvedValue({ data: { items: [], hasMore: false } })

    await chat.flushQueuedMessageInBackground('session-1')

    expect(api.post).toHaveBeenCalledWith('/sessions/session-1/messages', {
      content: 'Im Hintergrund'
    })
    expect(chat.queuedMessages['session-1']).toBeUndefined()
  })

  it('retries a background send while the other session is still running', async () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.queueMessage('Noch nicht')
    chat.selectedSessionId = 'session-2'
    api.post.mockRejectedValue({
      response: { status: 409, data: { error: { code: 'session.runAlreadyActive' } } }
    })

    await chat.flushQueuedMessageInBackground('session-1')

    expect(chat.queuedMessages['session-1']).toHaveLength(1)
    expect(chat.queuedSendPausedSessions['session-1']).toBeUndefined()
    chat.cancelBackgroundQueueFlush('session-1')
  })

  it('counts queued attachments against the global draft budget', () => {
    const chat = useChatStore()
    activeSession(chat)
    chat.running = true
    chat.attachmentDrafts.sessionMessages['session-1'] = [
      { id: 'a1', file: { size: MAX_ATTACHMENT_DRAFT_BYTES }, objectUrl: 'blob:preview' }
    ]
    chat.queueMessage('Groß')

    const result = chat.addAttachmentFiles([{ name: 'bild.png', type: 'image/png', size: 1024 }])

    expect(result.added).toBe(0)
    expect(result.error).toContain('256 MiB')
  })
})
