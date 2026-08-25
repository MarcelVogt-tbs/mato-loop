import 'fake-indexeddb/auto'
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
import { useControlCenterStore } from '../src/stores/control-center'
import { useQuestionStore } from '../src/stores/questions'
import { useWorkspaceStore } from '../src/stores/workspace'
import { clearChatOutbox, loadChatOutbox } from '../src/utils/chat-outbox'

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

  it('keeps an idle submission queued until the server accepts it', async () => {
    let resolveSend
    api.post.mockReturnValue(new Promise((resolve) => { resolveSend = resolve }))
    const chat = useChatStore()
    activeSession(chat)
    vi.spyOn(chat, 'watchRun').mockImplementation(() => {})

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

    expect(chat.activeQueuedMessages).toEqual([])
    expect(chat.current.messages).toEqual([
      { id: 'message-1', role: 'user', content: 'Jetzt senden' }
    ])
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

  it('queues the first message again immediately after undoing it', async () => {
    const chat = useChatStore()
    const message = { id: 'message-1', role: 'user', content: 'Noch einmal', attachments: [] }
    chat.current = {
      id: 'session-1',
      messages: [message],
      workspaceContext: { connected: true, operations: ['filesystem.list'] }
    }
    chat.selectedSessionId = 'session-1'
    api.get.mockResolvedValue({ data: [] })
    api.post.mockResolvedValue({
      data: { sessionDeleted: true, draft: { content: message.content } }
    })

    await chat.undoUserMessage(message)
    const queued = await chat.queueMessage(chat.activeDraft)

    expect(queued).not.toBeNull()
    expect(queued.options.workspaceContext).toBeNull()

    setActivePinia(createPinia())
    const restored = useChatStore()
    restored.setDraftOwner('user-1')
    await restored.outboxLoadPromise
    restored.cancelQueueDrain()

    expect(restored.activeQueuedMessages).toHaveLength(1)
    expect(restored.activeQueuedMessages[0]).toMatchObject({
      id: queued.id,
      content: 'Noch einmal',
      options: { workspaceContext: null }
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
    workspace.rootIdentity = '550e8400-e29b-41d4-a716-446655440009'
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
      root_identity: '550e8400-e29b-41d4-a716-446655440009',
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
    const projectEntry = await chat.queueMessage('Projekt löschen')
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
    expect((await loadChatOutbox('user-1')).some((entry) => entry.id === projectEntry.id)).toBe(false)
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
