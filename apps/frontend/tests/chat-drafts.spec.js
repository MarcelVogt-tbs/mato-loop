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

import { MAX_ATTACHMENT_DRAFT_BYTES, useChatStore } from '../src/stores/chat'

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
  static instances = []

  constructor(url) {
    this.url = url
    this.listeners = new Map()
    EventSourceMock.instances.push(this)
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener)
  }

  emit(name, payload) {
    this.listeners.get(name)?.({ data: JSON.stringify(payload) })
  }

  close() {}
}

describe('chat drafts', () => {
  beforeEach(() => {
    global.window = { localStorage: createStorage() }
    setActivePinia(createPinia())
    vi.clearAllMocks()
    EventSourceMock.instances = []
  })

  afterEach(() => {
    vi.useRealTimers()
    delete global.window
    delete global.EventSource
    global.URL = originalURL
  })

  it('keeps separate drafts for new and existing sessions', () => {
    const chat = useChatStore()

    chat.setDraft('Neue Session Entwurf')
    chat.current = { id: 'session-1' }
    chat.setDraft('Bestehende Session Entwurf')

    expect(chat.activeDraft).toBe('Bestehende Session Entwurf')
    chat.current = null
    expect(chat.activeDraft).toBe('Neue Session Entwurf')
  })

  it('keeps activity time stable until the visible work changes', () => {
    const chat = useChatStore()
    const modelStartedAt = '2026-07-27T12:00:00.000Z'
    const repeatedEventAt = '2026-07-27T12:00:05.000Z'
    const firstToolStartedAt = '2026-07-27T12:00:10.000Z'
    const secondToolStartedAt = '2026-07-27T12:00:12.000Z'

    chat.setActivity('waiting_model', { modelRequest: 2, startedAt: modelStartedAt })
    chat.setActivity('waiting_model', { modelRequest: 3, startedAt: repeatedEventAt })
    expect(chat.activity.startedAt).toBe(modelStartedAt)

    chat.setActivity('processing_tool', {
      tool: 'read_file', toolCallId: 'call-1', index: 0, total: 2, startedAt: firstToolStartedAt
    })
    expect(chat.activity).toMatchObject({
      startedAt: firstToolStartedAt,
      tool: 'read_file',
      toolCallId: 'call-1',
      index: 0,
      total: 2
    })

    chat.setActivity('processing_tool', {
      tool: 'read_file', index: 1, total: 2, startedAt: secondToolStartedAt
    })
    expect(chat.activity.startedAt).toBe(secondToolStartedAt)
  })

  it('restores drafts from local storage without a schema version', () => {
    const first = useChatStore()
    first.setDraft('Bleibt lokal erhalten')

    setActivePinia(createPinia())
    const restored = useChatStore()

    expect(restored.activeDraft).toBe('Bleibt lokal erhalten')
    const stored = JSON.parse(window.localStorage.getItem('matoLoopChatDrafts'))
    expect(stored.version).toBeUndefined()
  })

  it('migrates the persisted forced-search draft to the required policy', () => {
    window.localStorage.setItem('matoLoopChatDrafts', JSON.stringify({
      newSession: { message: '', options: { forceWebSearch: true } },
      sessionMessages: {}
    }))

    const chat = useChatStore()

    expect(chat.activeContext.webSearchPolicy).toBe('required')
  })

  it('sends the selected web-search policy when creating a session', async () => {
    api.post.mockResolvedValue({
      data: {
        session: { id: 'session-1', webSearchPolicy: 'required', messages: [] },
        runId: 'run-1'
      }
    })
    const chat = useChatStore()
    vi.spyOn(chat, 'watchRun').mockImplementation(() => {})
    await chat.updateActive({ web_search_policy: 'required' })

    await chat.startSession('Bitte recherchieren')

    expect(api.post).toHaveBeenCalledWith('/sessions', expect.objectContaining({
      web_search_policy: 'required'
    }))
  })

  it('keeps attachments out of local storage and uses the general multipart route', async () => {
    api.post.mockResolvedValue({
      data: {
        session: { id: 'session-audio', messages: [] },
        message: {
          id: 'message-audio',
          role: 'user',
          content: 'Bitte transkribieren',
          attachments: [{ id: 'attachment-audio', kind: 'audio', mimeType: 'audio/wav' }]
        },
        runId: 'run-audio'
      }
    })
    const audio = new Blob(['RIFF----WAVE'], { type: 'audio/wav' })
    Object.defineProperty(audio, 'name', { value: 'aufnahme.wav' })
    const notes = new Blob(['Notizen'], { type: 'text/plain' })
    Object.defineProperty(notes, 'name', { value: 'notizen.txt' })
    const chat = useChatStore()
    vi.spyOn(chat, 'watchRun').mockImplementation(() => {})

    expect(chat.addAttachmentFiles([audio, notes])).toMatchObject({ added: 2, error: null })
    chat.setDraft('Bitte transkribieren')
    const persisted = window.localStorage.getItem('matoLoopChatDrafts')
    expect(persisted).not.toContain('aufnahme.wav')

    await chat.send('Bitte transkribieren')

    expect(api.post).toHaveBeenCalledWith('/sessions/with-attachments', expect.any(FormData))
    const formData = api.post.mock.calls[0][1]
    expect(JSON.parse(formData.get('payload'))).toMatchObject({ content: 'Bitte transkribieren' })
    expect(formData.getAll('attachments')).toHaveLength(2)
    expect(formData.getAll('attachments').map((file) => file.name))
      .toEqual(['aufnahme.wav', 'notizen.txt'])
    expect(chat.attachmentDrafts.newSession).toEqual([])
    expect(chat.current.messages[0].attachments).toHaveLength(1)
  })

  it('uses the message multipart route and retains a generic file after an API failure', async () => {
    api.post.mockRejectedValue(new Error('offline'))
    const document = new Blob(['PDF'], { type: 'application/pdf' })
    Object.defineProperty(document, 'name', { value: 'fehler.pdf' })
    const chat = useChatStore()
    chat.current = { id: 'session-1', messages: [] }
    chat.addAttachmentFiles([document])

    await expect(chat.send('Bitte prüfen')).rejects.toThrow('offline')

    expect(api.post).toHaveBeenCalledWith(
      '/sessions/session-1/messages/with-attachments',
      expect.any(FormData)
    )
    expect(chat.activeAttachmentDraft).toHaveLength(1)
    expect(chat.current.messages).toEqual([])
  })

  it('accepts generic files and rejects too many, empty and oversized attachments', () => {
    const chat = useChatStore()
    const wav = () => {
      const file = new Blob(['wav'], { type: 'audio/wav' })
      Object.defineProperty(file, 'name', { value: 'audio.wav' })
      return file
    }

    expect(chat.addAttachmentFiles([wav(), wav(), wav(), wav(), wav()]).error).toContain('höchstens vier')
    expect(chat.addAttachmentFiles([{ name: 'leer.txt', type: 'text/plain', size: 0 }]).error)
      .toContain('leer')
    expect(chat.addAttachmentFiles([{
      name: 'gross.pdf',
      type: 'application/pdf',
      size: 64 * 1024 * 1024 + 1
    }]).error).toContain('64 MiB')
    expect(chat.addAttachmentFiles([{ name: 'notizen.txt', type: 'text/plain', size: 10 }]))
      .toMatchObject({ added: 1, error: null })
    expect(chat.activeAttachmentDraft[0]).toMatchObject({ kind: 'file', mimeType: 'text/plain' })
    expect(chat.activeAttachmentDraft[0]).not.toHaveProperty('uploadReady')
  })

  it('sends generic drafts without a temporary upload block', async () => {
    api.post.mockResolvedValue({
      data: {
        session: { id: 'session-file', messages: [] },
        runId: 'run-file'
      }
    })
    const notes = new Blob(['Inhalt'], { type: 'text/plain' })
    Object.defineProperty(notes, 'name', { value: 'notizen.txt' })
    const chat = useChatStore()
    vi.spyOn(chat, 'watchRun').mockImplementation(() => {})
    chat.addAttachmentFiles([notes])

    await expect(chat.send('Bitte lesen')).resolves.toMatchObject({ created: true })

    expect(api.post).toHaveBeenCalledWith('/sessions/with-attachments', expect.any(FormData))
    expect(chat.activeAttachmentDraft).toHaveLength(0)
  })

  it('limits attachment drafts across sessions and does not preview non-images', () => {
    const createObjectURL = vi.fn()
    global.URL = { createObjectURL, revokeObjectURL: vi.fn() }
    const chat = useChatStore()
    const fullFile = (name) => ({
      name: `${name}.wav`,
      type: 'audio/wav',
      size: MAX_ATTACHMENT_DRAFT_BYTES / 4
    })

    expect(chat.addAttachmentFiles([fullFile('neu')])).toMatchObject({ added: 1, error: null })
    for (const id of ['session-1', 'session-2', 'session-3']) {
      chat.current = { id }
      expect(chat.addAttachmentFiles([fullFile(id)])).toMatchObject({ added: 1, error: null })
    }
    chat.current = { id: 'session-4' }
    expect(chat.addAttachmentFiles([{ name: 'zwei.wav', type: 'audio/wav', size: 1 }]).error)
      .toContain('256 MiB')
    expect(createObjectURL).not.toHaveBeenCalled()
  })

  it('creates image previews and revokes them when removing or clearing drafts', () => {
    const createObjectURL = vi.fn(() => 'blob:preview')
    const revokeObjectURL = vi.fn()
    global.URL = { createObjectURL, revokeObjectURL }
    const chat = useChatStore()

    chat.addAttachmentFiles([{ name: 'bild.png', type: 'image/png', size: 10 }])

    expect(chat.activeAttachmentDraft[0]).toMatchObject({
      kind: 'image',
      objectUrl: 'blob:preview'
    })
    expect(chat.activeAttachmentDraft[0]).not.toHaveProperty('uploadReady')
    chat.removeAttachmentFile(chat.activeAttachmentDraft[0].id)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:preview')
  })

  it('clears every attachment draft and revokes preview URLs', () => {
    const revokeObjectURL = vi.fn()
    global.URL = { revokeObjectURL }
    const chat = useChatStore()
    chat.attachmentDrafts = {
      newSession: [{ id: 'new', file: { size: 1 }, objectUrl: 'blob:new' }],
      sessionMessages: {
        'session-1': [{ id: 'old', file: { size: 1 }, objectUrl: 'blob:old' }]
      }
    }
    chat.setAudioDictation({ file: { size: 2 }, status: 'failed' }, 'session-1')

    chat.clearAllAttachmentDrafts()

    expect(chat.attachmentDrafts).toEqual({ newSession: [], sessionMessages: {} })
    expect(chat.audioDictations).toEqual({ newSession: null, sessionMessages: {} })
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:new')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:old')
  })

  it('keeps the effective memory mode in sync for a new session draft', async () => {
    const chat = useChatStore()

    await chat.updateActive({ memory_mode: 'read_only' })

    expect(chat.activeContext.memoryMode).toBe('read_only')
    expect(chat.activeContext.effectiveMemoryMode).toBe('read_only')
  })

  it('keeps instant memory active without model tool support', async () => {
    const chat = useChatStore()
    chat.activeContext.memoryUnavailableReason = 'memory.modelToolsUnavailable'
    chat.activeContext.memoryToolsAvailable = false

    await chat.updateActive({ memory_mode: 'read_only' })

    expect(chat.activeContext.effectiveMemoryMode).toBe('read_only')
  })

  it('refreshes effective memory when the draft model changes', async () => {
    api.get.mockResolvedValue({
      data: {
        modelId: 'private-model',
        effectiveMemoryMode: 'disabled',
        memoryToolsAvailable: false,
        memoryUnavailableReason: 'memory.modelDenied',
        trustLevel: 'private'
      }
    })
    const chat = useChatStore()

    await chat.updateActive({ model_id: 'private-model' })

    expect(api.get).toHaveBeenCalledWith('/sessions/defaults', {
      params: { modelId: 'private-model', memoryMode: 'full' }
    })
    expect(chat.activeContext).toMatchObject({
      modelId: 'private-model',
      effectiveMemoryMode: 'disabled',
      memoryToolsAvailable: false,
      memoryUnavailableReason: 'memory.modelDenied',
      trustLevel: 'private'
    })
  })

  it('preserves the draft memory mode while refreshing defaults', async () => {
    const chat = useChatStore()
    chat.activeContext.modelId = 'saved-model'
    chat.activeContext.memoryMode = 'read_only'
    api.get
      .mockResolvedValueOnce({ data: { modelId: 'default-model', memoryMode: 'full' } })
      .mockResolvedValueOnce({
        data: {
          effectiveMemoryMode: 'read_only',
          memoryToolsAvailable: true,
          memoryUnavailableReason: null,
          trustLevel: 'private'
        }
      })

    await chat.fetchDefaults()

    expect(api.get).toHaveBeenLastCalledWith('/sessions/defaults', {
      params: { modelId: 'saved-model', memoryMode: 'read_only' }
    })
    expect(chat.activeContext).toMatchObject({
      modelId: 'saved-model',
      memoryMode: 'read_only',
      effectiveMemoryMode: 'read_only',
      trustLevel: 'private'
    })
  })

  it('applies effective defaults when a persisted model is removed', () => {
    const chat = useChatStore()
    chat.sessionDefaults = {
      modelId: 'default-model',
      effectiveMemoryMode: 'full',
      memoryToolsAvailable: true,
      memoryUnavailableReason: null,
      trustLevel: 'private'
    }
    Object.assign(chat.activeContext, {
      modelId: 'removed-model',
      memoryMode: 'read_only',
      effectiveMemoryMode: 'disabled',
      memoryToolsAvailable: false,
      memoryUnavailableReason: 'session.modelUnavailable',
      trustLevel: 'public'
    })

    chat.reconcileNewSession(['default-model'], true)

    expect(chat.activeContext).toMatchObject({
      modelId: 'default-model',
      memoryMode: 'read_only',
      effectiveMemoryMode: 'read_only',
      memoryToolsAvailable: true,
      memoryUnavailableReason: null,
      trustLevel: 'private'
    })
  })

  it('ignores a stale session response after navigation', async () => {
    const requests = {}
    api.get.mockImplementation((url, config) => new Promise((resolve) => {
      requests[url] = { resolve, config }
    }))
    const chat = useChatStore()
    chat.current = { id: 'previous-session', messages: [{ id: 'old-message' }] }

    const firstLoad = chat.loadSession('session-1')
    expect(chat.current).toBeNull()
    const secondLoad = chat.loadSession('session-2')
    expect(requests['/sessions/session-1'].config.signal.aborted).toBe(true)
    requests['/sessions/session-2'].resolve({
      data: { id: 'session-2', messages: [], activeRun: null }
    })
    await secondLoad
    requests['/sessions/session-1'].resolve({
      data: { id: 'session-1', messages: [], activeRun: null }
    })
    await firstLoad

    expect(chat.current.id).toBe('session-2')
    expect(chat.loading).toBe(false)
    expect(chat.loadError).toBeNull()
  })

  it('does not attach a late send response to a newly selected session', async () => {
    global.EventSource = EventSourceMock
    let resolveSend
    api.post.mockReturnValue(new Promise((resolve) => { resolveSend = resolve }))
    api.get.mockImplementation((url) => Promise.resolve({
      data: url === '/sessions/session-2'
        ? { id: 'session-2', messages: [], activeRun: null }
        : []
    }))
    const chat = useChatStore()
    chat.current = { id: 'session-1', messages: [] }
    chat.selectedSessionId = 'session-1'
    const watchRun = vi.spyOn(chat, 'watchRun')

    const sending = chat.sendToCurrent('Nachricht an Session 1')
    await chat.loadSession('session-2')
    resolveSend({
      data: {
        runId: 'run-1',
        message: { id: 'message-1', role: 'user', content: 'Nachricht an Session 1' }
      }
    })
    await sending

    expect(chat.current.id).toBe('session-2')
    expect(chat.selectedSessionId).toBe('session-2')
    expect(chat.running).toBe(false)
    expect(watchRun).not.toHaveBeenCalled()
  })

  it('timestamps an optimistic user message immediately', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-25T14:32:00Z'))
    let resolveSend
    api.post.mockReturnValue(new Promise((resolve) => { resolveSend = resolve }))
    const chat = useChatStore()
    chat.current = { id: 'session-1', messages: [] }
    chat.selectedSessionId = 'session-1'
    vi.spyOn(chat, 'watchRun').mockImplementation(() => {})

    const sending = chat.sendToCurrent('Neue Nachricht')

    expect(chat.current.messages[0].createdAt).toBe('2026-07-25T14:32:00.000Z')
    resolveSend({ data: { runId: 'run-1' } })
    await sending
  })

  it('moves a continued session to the top after persisting its new message', async () => {
    api.post.mockResolvedValue({
      data: {
        runId: 'run-1',
        message: {
          id: 'message-1',
          role: 'user',
          content: 'Neue Nachricht',
          createdAt: '2026-07-24T15:00:00Z'
        }
      }
    })
    const chat = useChatStore()
    vi.spyOn(chat, 'watchRun').mockImplementation(() => {})
    chat.sessions = [
      { id: 'session-2', lastMessageAt: '2026-07-24T14:00:00Z' },
      { id: 'session-1', lastMessageAt: '2026-07-20T12:00:00Z' }
    ]
    chat.current = {
      id: 'session-1',
      lastMessageAt: '2026-07-20T12:00:00Z',
      messages: []
    }
    chat.selectedSessionId = 'session-1'

    await chat.sendToCurrent('Neue Nachricht')

    expect(chat.sessions.map((session) => session.id)).toEqual(['session-1', 'session-2'])
    expect(chat.sessions[0].lastMessageAt).toBe('2026-07-24T15:00:00Z')
    expect(chat.current.lastMessageAt).toBe('2026-07-24T15:00:00Z')
  })

  it('does not navigate back to a late new-session response', async () => {
    let resolveSend
    api.post.mockReturnValue(new Promise((resolve) => { resolveSend = resolve }))
    api.get.mockResolvedValue({
      data: { id: 'session-2', messages: [], activeRun: null }
    })
    const chat = useChatStore()
    const watchRun = vi.spyOn(chat, 'watchRun')

    const sending = chat.startSession('Neue Nachricht')
    await chat.loadSession('session-2')
    resolveSend({
      data: {
        session: { id: 'new-session', title: 'Neue Nachricht' },
        message: { id: 'message-new', role: 'user', content: 'Neue Nachricht' },
        runId: 'run-new'
      }
    })
    const result = await sending

    expect(result).toEqual({ created: false, sessionId: 'new-session' })
    expect(chat.current.id).toBe('session-2')
    expect(chat.selectedSessionId).toBe('session-2')
    expect(watchRun).not.toHaveBeenCalled()
  })

  it('does not merge a late settings response into another session', async () => {
    let resolveUpdate
    api.patch.mockReturnValue(new Promise((resolve) => { resolveUpdate = resolve }))
    const chat = useChatStore()
    chat.current = { id: 'session-1', title: 'Eins' }
    chat.selectedSessionId = 'session-1'

    const updating = chat.updateCurrent({ title: 'Neu' })
    chat.current = { id: 'session-2', title: 'Zwei' }
    chat.selectedSessionId = 'session-2'
    resolveUpdate({ data: { id: 'session-1', title: 'Neu' } })
    await updating

    expect(chat.current).toEqual({ id: 'session-2', title: 'Zwei' })
  })

  it('keeps the latest session loading when an aborted request rejects', async () => {
    const requests = {}
    api.get.mockImplementation((url, config) => new Promise((resolve, reject) => {
      requests[url] = { resolve, reject, config }
    }))
    const chat = useChatStore()

    const firstLoad = chat.loadSession('session-1')
    const secondLoad = chat.loadSession('session-2')
    await chat.refreshSession('session-2')
    expect(requests['/sessions/session-2'].config.signal.aborted).toBe(false)
    requests['/sessions/session-1'].reject(new Error('aborted'))
    await firstLoad

    expect(chat.loading).toBe(true)
    expect(chat.loadError).toBeNull()
    requests['/sessions/session-2'].resolve({
      data: { id: 'session-2', messages: [], activeRun: null }
    })
    await secondLoad

    expect(chat.current.id).toBe('session-2')
    expect(chat.loading).toBe(false)
  })

  it('resumes the active run when loading a session', async () => {
    global.EventSource = EventSourceMock
    api.get.mockResolvedValue({
      data: {
        id: 'session-1',
        messages: [],
        activeRun: {
          id: 'run-1',
          status: 'running',
          modelName: 'Aktives Modell',
          createdAt: '2026-07-19T12:00:00Z'
        }
      }
    })
    const chat = useChatStore()

    await chat.loadSession('session-1')

    expect(chat.running).toBe(true)
    expect(chat.streamModelName).toBe('Aktives Modell')
    expect(chat.eventSource.url).toBe('/api/sessions/session-1/runs/run-1/events')
    expect(chat.activity).toMatchObject({
      phase: 'preparing_context',
      startedAt: '2026-07-19T12:00:00Z'
    })
  })

  it('tracks model, streaming and approval phases from SSE', () => {
    global.EventSource = EventSourceMock
    const chat = useChatStore()
    chat.current = { id: 'session-1', messages: [] }
    chat.sessions = [{ id: 'session-1', title: 'Erste Nachricht' }]
    chat.selectedSessionId = 'session-1'

    chat.watchRun('session-1', 'run-1', {
      status: 'queued',
      createdAt: '2026-07-19T12:00:00Z'
    })
    const source = EventSourceMock.instances[0]
    source.emit('run.started', {
      modelName: 'Streaming-Modell',
      createdAt: '2026-07-19T12:00:00Z'
    })
    expect(chat.activity.phase).toBe('preparing_context')
    expect(chat.streamModelName).toBe('Streaming-Modell')

    source.emit('run.phase', { phase: 'waiting_model', modelRequest: 1 })
    expect(chat.activity).toMatchObject({ phase: 'waiting_model', modelRequest: 1 })

    source.emit('reasoning.updated', {
      reasoning: { kind: 'content', text: 'Plan prüfen', status: 'streaming' }
    })
    expect(chat.streamReasoning.text).toBe('Plan prüfen')

    source.emit('message.delta', { delta: 'Hallo' })
    expect(chat.activity.phase).toBe('streaming')
    expect(chat.streamContent).toBe('Hallo')

    source.emit('session.title.updated', {
      sessionId: 'session-1',
      title: 'Generierter Titel'
    })
    expect(chat.current.title).toBe('Generierter Titel')
    expect(chat.current.titleGenerationStatus).toBe('completed')
    expect(chat.sessions[0].title).toBe('Generierter Titel')
    expect(chat.sessions[0].titleGenerationStatus).toBe('completed')

    source.emit('approval.created', {
      approval: { id: 'approval-1', details: { tool: 'write_file', toolCallId: 'call-1' } }
    })
    expect(chat.activity).toMatchObject({
      phase: 'waiting_approval',
      tool: 'write_file',
      toolCallId: 'call-1'
    })
  })

  it('promotes streamed content before refreshing the persisted message', async () => {
    global.EventSource = EventSourceMock
    let resolveRefresh
    api.get.mockImplementation(() => new Promise((resolve) => {
      resolveRefresh = resolve
    }))
    const chat = useChatStore()
    chat.current = { id: 'session-1', messages: [] }
    chat.selectedSessionId = 'session-1'
    chat.watchRun('session-1', 'run-1', { status: 'running' })
    const source = EventSourceMock.instances[0]

    source.emit('message.delta', { delta: 'Vollständige Antwort' })
    source.emit('message.completed', {
      message: {
        id: 'message-1',
        role: 'assistant',
        content: 'Abgeschnitten',
        createdAt: '2026-07-25T14:32:00Z',
        modelName: 'Antwortmodell',
        research: {
          policy: 'available',
          used: true,
          linkedSources: [{ title: 'Quelle', url: 'https://example.test/source' }]
        },
        toolCalls: []
      }
    })

    expect(chat.streamContent).toBe('')
    expect(chat.current.messages[0]).toMatchObject({
      id: 'message-1',
      content: 'Vollständige Antwort',
      createdAt: '2026-07-25T14:32:00Z',
      modelName: 'Antwortmodell',
      research: {
        linkedSources: [{ title: 'Quelle', url: 'https://example.test/source' }]
      }
    })
    expect(chat.streamModelName).toBe('Antwortmodell')
    resolveRefresh({
      data: {
        id: 'session-1',
        messages: [{ id: 'message-1', role: 'assistant', content: 'Serverstand' }]
      }
    })
    await vi.waitFor(() => expect(chat.current.messages[0].content).toBe('Serverstand'))
  })

  it('refreshes a generated title after the first run', async () => {
    vi.useFakeTimers()
    api.get.mockResolvedValue({
      data: [{
        id: 'session-1',
        title: 'Standardmodelle konfigurieren',
        titleGenerationStatus: 'completed'
      }]
    })
    const chat = useChatStore()
    chat.current = {
      id: 'session-1',
      title: 'Default Models einbauen',
      titleGenerationStatus: 'running',
      messages: []
    }
    chat.sessions = [{
      id: 'session-1',
      title: 'Default Models einbauen',
      titleGenerationStatus: 'running'
    }]

    const refresh = chat.refreshGeneratedTitle('session-1')
    await vi.advanceTimersByTimeAsync(1000)
    await refresh

    expect(chat.current.title).toBe('Standardmodelle konfigurieren')
    expect(chat.current.titleGenerationStatus).toBe('completed')
    expect(api.get).toHaveBeenCalledWith('/sessions')
  })

  it('stops generated title polling after a terminal failure', async () => {
    vi.useFakeTimers()
    api.get.mockResolvedValue({
      data: [{
        id: 'session-1',
        title: 'Lokaler Titel',
        titleGenerationStatus: 'failed'
      }]
    })
    const chat = useChatStore()
    chat.current = {
      id: 'session-1',
      title: 'Lokaler Titel',
      titleGenerationStatus: 'running',
      messages: []
    }
    chat.sessions = [{
      id: 'session-1',
      title: 'Lokaler Titel',
      titleGenerationStatus: 'running'
    }]

    const refresh = chat.refreshGeneratedTitle('session-1')
    await vi.advanceTimersByTimeAsync(1000)
    await refresh

    expect(chat.current.titleGenerationStatus).toBe('failed')
    expect(api.get).toHaveBeenCalledTimes(1)
  })

  it('renames a session and keeps the list and current session in sync', async () => {
    api.patch.mockResolvedValue({
      data: {
        id: 'session-1',
        title: 'Neuer Titel',
        updatedAt: '2026-07-20T14:00:00Z'
      }
    })
    const chat = useChatStore()
    chat.sessions = [
      { id: 'session-2', title: 'Andere Session', lastMessageAt: '2026-07-20T13:00:00Z' },
      { id: 'session-1', title: 'Alter Titel', lastMessageAt: '2026-07-19T13:00:00Z' }
    ]
    chat.current = { id: 'session-1', title: 'Alter Titel', messages: [] }

    await chat.renameSession('session-1', 'Neuer Titel')

    expect(api.patch).toHaveBeenCalledWith('/sessions/session-1', { title: 'Neuer Titel' })
    expect(chat.sessions.map((session) => session.id)).toEqual(['session-2', 'session-1'])
    expect(chat.current.title).toBe('Neuer Titel')
  })

  it('pins a session and moves it ahead of unpinned sessions', async () => {
    api.patch.mockResolvedValue({
      data: {
        id: 'session-1',
        pinned: true,
        updatedAt: '2026-07-20T12:00:00Z'
      }
    })
    const chat = useChatStore()
    chat.sessions = [
      { id: 'session-2', pinned: false, lastMessageAt: '2026-07-20T14:00:00Z' },
      { id: 'session-1', pinned: false, lastMessageAt: '2026-07-20T12:00:00Z' }
    ]
    chat.current = { id: 'session-1', pinned: false, messages: [] }

    await chat.setSessionPinned('session-1', true)

    expect(api.patch).toHaveBeenCalledWith('/sessions/session-1', { pinned: true })
    expect(chat.sessions.map((session) => session.id)).toEqual(['session-1', 'session-2'])
    expect(chat.current.pinned).toBe(true)
  })

  it('keeps the run active and reconnects after a transient SSE error', async () => {
    vi.useFakeTimers()
    global.EventSource = EventSourceMock
    api.get.mockResolvedValue({
      data: {
        id: 'run-1',
        status: 'running',
        error: null,
        createdAt: '2026-07-19T12:00:00Z'
      }
    })
    const chat = useChatStore()
    chat.current = { id: 'session-1', messages: [] }
    chat.selectedSessionId = 'session-1'
    chat.watchRun('session-1', 'run-1', {
      status: 'running',
      createdAt: '2026-07-19T12:00:00Z'
    })

    await EventSourceMock.instances[0].onerror()

    expect(chat.running).toBe(true)
    expect(chat.activity.phase).toBe('reconnecting')
    await vi.advanceTimersByTimeAsync(1000)
    expect(EventSourceMock.instances).toHaveLength(2)
    expect(chat.running).toBe(true)
  })

  it('stops an active run and waits for the terminal event', async () => {
    global.EventSource = EventSourceMock
    api.post.mockResolvedValue({ data: { status: 'cancelled' } })
    api.get.mockImplementation((url) => Promise.resolve({
      data: url === '/sessions/session-1'
        ? { id: 'session-1', messages: [], activeRun: null }
        : []
    }))
    const chat = useChatStore()
    chat.current = { id: 'session-1', messages: [] }
    chat.selectedSessionId = 'session-1'
    chat.watchRun('session-1', 'run-1', { status: 'running' })

    await chat.stop()

    expect(api.post).toHaveBeenCalledWith('/sessions/session-1/runs/run-1/cancel')
    expect(chat.running).toBe(true)
    expect(chat.stopping).toBe(true)

    EventSourceMock.instances[0].emit('run.cancelled', { runId: 'run-1' })

    expect(chat.running).toBe(false)
    expect(chat.stopping).toBe(false)
    expect(chat.runId).toBeNull()
  })

  it('reloads the persisted runtime error after a failed run', async () => {
    global.EventSource = EventSourceMock
    api.get.mockImplementation((url) => Promise.resolve({
      data: url === '/sessions/session-1'
        ? {
            id: 'session-1',
            activeRun: null,
            messages: [{
              id: 'error-1',
              role: 'assistant',
              kind: 'runtime_error',
              error: {
                code: 'agent.requiredWebSearchMissing',
                message: 'The required web search was not performed.',
                params: {}
              }
            }]
          }
        : []
    }))
    const chat = useChatStore()
    chat.current = { id: 'session-1', messages: [] }
    chat.selectedSessionId = 'session-1'
    chat.watchRun('session-1', 'run-1', { status: 'running' })

    EventSourceMock.instances[0].emit('run.failed', {
      error: { code: 'agent.requiredWebSearchMissing' }
    })

    await vi.waitFor(() => expect(chat.current.messages[0]).toMatchObject({
      kind: 'runtime_error',
      error: { code: 'agent.requiredWebSearchMissing' }
    }))
    expect(chat.running).toBe(false)
  })

  it('promotes the last streamed state before a cancelled run finishes', () => {
    global.EventSource = EventSourceMock
    api.get.mockImplementation(() => new Promise(() => {}))
    const chat = useChatStore()
    chat.current = { id: 'session-1', messages: [] }
    chat.selectedSessionId = 'session-1'
    chat.watchRun('session-1', 'run-1', { status: 'running' })
    const source = EventSourceMock.instances[0]

    source.emit('message.delta', { delta: 'Antwortende' })
    source.emit('reasoning.updated', {
      reasoning: { kind: 'content', text: 'Zwischenstand', status: 'streaming' }
    })
    source.emit('message.interrupted', {
      message: {
        id: 'message-1',
        role: 'assistant',
        content: 'Vollständige Teilantwort',
        generationStatus: 'interrupted',
        reasoning: { kind: 'content', text: 'Zwischenstand', status: 'interrupted' }
      }
    })
    source.emit('run.cancelled', { runId: 'run-1' })

    expect(chat.current.messages).toEqual([
      expect.objectContaining({
        id: 'message-1',
        content: 'Vollständige Teilantwort',
        generationStatus: 'interrupted',
        reasoning: expect.objectContaining({ text: 'Zwischenstand' })
      })
    ])
    expect(chat.streamContent).toBe('')
    expect(chat.streamReasoning).toBeNull()
    expect(chat.running).toBe(false)
  })

  it('keeps an empty interruption marker when no text was streamed', () => {
    global.EventSource = EventSourceMock
    const chat = useChatStore()
    chat.current = { id: 'session-1', messages: [] }
    chat.selectedSessionId = 'session-1'
    chat.watchRun('session-1', 'run-1', { status: 'running' })

    EventSourceMock.instances[0].emit('message.interrupted', {
      message: {
        id: 'message-1',
        role: 'assistant',
        content: '',
        generationStatus: 'interrupted',
        reasoning: null
      }
    })

    expect(chat.current.messages).toEqual([
      expect.objectContaining({
        id: 'message-1',
        content: '',
        generationStatus: 'interrupted'
      })
    ])
  })

  it('queues an immediate stop until the run id is known', async () => {
    global.EventSource = EventSourceMock
    let resolveCreate
    api.post.mockImplementation((url) => {
      if (url === '/sessions') {
        return new Promise((resolve) => {
          resolveCreate = resolve
        })
      }
      return Promise.resolve({ data: { status: 'cancelled' } })
    })
    const chat = useChatStore()

    const sending = chat.send('Bitte stoppen')
    await chat.stop()
    expect(chat.stopping).toBe(true)

    resolveCreate({
      data: {
        session: { id: 'session-1', messages: [] },
        runId: 'run-1'
      }
    })
    await sending

    expect(api.post).toHaveBeenNthCalledWith(2, '/sessions/session-1/runs/run-1/cancel')
    expect(chat.running).toBe(true)
    expect(chat.stopping).toBe(true)
  })

  it('undoes a user message and restores its text as the session draft', async () => {
    const canonicalSession = {
      id: 'session-1',
      title: 'Test',
      messages: [{ id: 'user-1', role: 'user', content: 'Bleibt' }],
      activeRun: null
    }
    api.get.mockImplementation((url) => Promise.resolve({
      data: url === '/sessions/session-1' ? canonicalSession : []
    }))
    api.post.mockResolvedValue({
      data: {
        sessionDeleted: false,
        session: canonicalSession,
        draft: { content: 'Noch einmal bearbeiten' }
      }
    })
    const chat = useChatStore()
    chat.sessions = [{ id: 'session-1', title: 'Test' }]
    chat.current = {
      id: 'session-1',
      title: 'Test',
      messages: [
        { id: 'user-1', role: 'user', content: 'Bleibt' },
        { id: 'user-2', role: 'user', content: 'Noch einmal bearbeiten', attachments: [] }
      ]
    }
    chat.selectedSessionId = 'session-1'
    chat.setDraft('Wird ersetzt')

    await chat.undoUserMessage(chat.current.messages[1])

    expect(api.post).toHaveBeenCalledWith(
      '/sessions/session-1/messages/user-2/undo',
      null,
      { suppressErrorToast: true }
    )
    expect(chat.current.messages).toEqual([{ id: 'user-1', role: 'user', content: 'Bleibt' }])
    expect(chat.activeDraft).toBe('Noch einmal bearbeiten')
  })

  it('restores persisted attachments before undoing the message', async () => {
    const sessionId = '11111111-1111-4111-8111-111111111111'
    const messageId = '22222222-2222-4222-8222-222222222222'
    const attachmentId = '33333333-3333-4333-8333-333333333333'
    const url = `/api/sessions/${sessionId}/messages/${messageId}/attachments/${attachmentId}`
    global.URL = { createObjectURL: vi.fn(() => 'blob:restored'), revokeObjectURL: vi.fn() }
    api.get.mockImplementation((requestUrl) => Promise.resolve({
      data: requestUrl === url
        ? new Blob(['inhalt'], { type: 'text/plain' })
        : requestUrl === `/sessions/${sessionId}`
          ? { id: sessionId, messages: [], activeRun: null }
          : []
    }))
    api.post.mockResolvedValue({
      data: {
        sessionDeleted: false,
        session: { id: sessionId, messages: [], activeRun: null },
        draft: { content: 'Mit Datei' }
      }
    })
    const chat = useChatStore()
    const message = {
      id: messageId,
      role: 'user',
      content: 'Mit Datei',
      attachments: [{ id: attachmentId, url, filename: 'notiz.txt', mimeType: 'text/plain' }]
    }
    chat.current = { id: sessionId, messages: [message] }
    chat.selectedSessionId = sessionId

    await chat.undoUserMessage(message)

    expect(chat.activeAttachmentDraft).toHaveLength(1)
    expect(chat.activeAttachmentDraft[0].file.name).toBe('notiz.txt')
    expect(chat.activeAttachmentDraft[0].file.type).toBe('text/plain')
  })

  it('keeps history and draft when undo fails', async () => {
    const error = new Error('request failed')
    error.response = { status: 409 }
    api.post.mockRejectedValue(error)
    const chat = useChatStore()
    const message = { id: 'user-1', role: 'user', content: 'Bleibt', attachments: [] }
    chat.current = { id: 'session-1', messages: [message] }
    chat.selectedSessionId = 'session-1'
    chat.setDraft('Vorhandener Entwurf')

    await expect(chat.undoUserMessage(message)).rejects.toThrow('request failed')

    expect(chat.current.messages).toEqual([message])
    expect(chat.activeDraft).toBe('Vorhandener Entwurf')
  })

  it('keeps an undone message as a draft after navigating away', async () => {
    let resolveUndo
    api.post.mockReturnValue(new Promise((resolve) => { resolveUndo = resolve }))
    api.get.mockResolvedValue({ data: [] })
    const chat = useChatStore()
    const message = { id: 'user-2', role: 'user', content: 'Später bearbeiten', attachments: [] }
    chat.sessions = [{ id: 'session-1' }, { id: 'session-2' }]
    chat.current = {
      id: 'session-1',
      messages: [{ id: 'user-1', role: 'user', content: 'Bleibt' }, message]
    }
    chat.selectedSessionId = 'session-1'

    const undoing = chat.undoUserMessage(message)
    chat.current = { id: 'session-2', messages: [] }
    chat.selectedSessionId = 'session-2'
    resolveUndo({ data: { sessionDeleted: false, draft: { content: 'Später bearbeiten' } } })
    await undoing

    expect(chat.current.id).toBe('session-2')
    expect(chat.drafts.sessionMessages['session-1']).toBe('Später bearbeiten')
  })

  it('recovers an undo whose response was lost after the server commit', async () => {
    api.post.mockRejectedValue(new Error('connection lost'))
    api.get.mockResolvedValue({
      data: {
        id: 'session-1',
        messages: [{ id: 'user-1', role: 'user', content: 'Bleibt' }],
        activeRun: null
      }
    })
    const chat = useChatStore()
    const message = { id: 'user-2', role: 'user', content: 'Wiederherstellen', attachments: [] }
    chat.current = {
      id: 'session-1',
      messages: [{ id: 'user-1', role: 'user', content: 'Bleibt' }, message]
    }
    chat.selectedSessionId = 'session-1'

    const result = await chat.undoUserMessage(message)

    expect(result.sessionDeleted).toBe(false)
    expect(chat.current.messages).toEqual([{ id: 'user-1', role: 'user', content: 'Bleibt' }])
    expect(chat.activeDraft).toBe('Wiederherstellen')
  })

  it('retries the latest runtime error without replacing the user draft', async () => {
    global.EventSource = EventSourceMock
    api.get.mockResolvedValue({ data: [] })
    api.post.mockResolvedValue({
      data: {
        runId: 'run-2',
        modelName: 'Originalmodell',
        session: {
          id: 'session-1',
          messages: [{ id: 'user-1', role: 'user', content: 'Ursprünglicher Auftrag' }],
          activeRun: { id: 'run-2', status: 'queued', createdAt: '2026-07-25T14:32:00Z' }
        }
      }
    })
    const chat = useChatStore()
    const error = { id: 'error-1', role: 'assistant', kind: 'runtime_error' }
    chat.current = {
      id: 'session-1',
      messages: [{ id: 'user-1', role: 'user', content: 'Ursprünglicher Auftrag' }, error]
    }
    chat.selectedSessionId = 'session-1'
    chat.setDraft('Nicht verändern')

    await chat.retryFailedMessage(error)

    expect(api.post).toHaveBeenCalledWith(
      '/sessions/session-1/messages/error-1/retry',
      null,
      { suppressErrorToast: true }
    )
    expect(chat.current.messages).toEqual([
      { id: 'user-1', role: 'user', content: 'Ursprünglicher Auftrag' }
    ])
    expect(chat.activeDraft).toBe('Nicht verändern')
    expect(chat.runId).toBe('run-2')
    expect(chat.running).toBe(true)
  })
})
