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

import { useChatStore } from '../src/stores/chat'

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
    api.get.mockImplementation((url) => new Promise((resolve) => {
      requests[url] = resolve
    }))
    const chat = useChatStore()

    const firstLoad = chat.loadSession('session-1')
    const secondLoad = chat.loadSession('session-2')
    requests['/sessions/session-2']({ data: { id: 'session-2', messages: [], activeRun: null } })
    await secondLoad
    requests['/sessions/session-1']({ data: { id: 'session-1', messages: [], activeRun: null } })
    await firstLoad

    expect(chat.current.id).toBe('session-2')
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
    expect(chat.sessions[0].title).toBe('Generierter Titel')

    source.emit('approval.created', {
      approval: { id: 'approval-1', details: { tool: 'write_file' } }
    })
    expect(chat.activity).toMatchObject({ phase: 'waiting_approval', tool: 'write_file' })
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
        modelName: 'Antwortmodell',
        toolCalls: []
      }
    })

    expect(chat.streamContent).toBe('')
    expect(chat.current.messages[0]).toMatchObject({
      id: 'message-1',
      content: 'Vollständige Antwort',
      modelName: 'Antwortmodell'
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
      data: [{ id: 'session-1', title: 'Standardmodelle konfigurieren' }]
    })
    const chat = useChatStore()
    chat.current = { id: 'session-1', title: 'Default Models einbauen', messages: [] }
    chat.sessions = [{ id: 'session-1', title: 'Default Models einbauen' }]

    const refresh = chat.refreshGeneratedTitle('session-1', 'Default Models einbauen')
    await vi.advanceTimersByTimeAsync(1000)
    await refresh

    expect(chat.current.title).toBe('Standardmodelle konfigurieren')
    expect(api.get).toHaveBeenCalledWith('/sessions')
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
      { id: 'session-2', title: 'Andere Session', updatedAt: '2026-07-20T13:00:00Z' },
      { id: 'session-1', title: 'Alter Titel', updatedAt: '2026-07-19T13:00:00Z' }
    ]
    chat.current = { id: 'session-1', title: 'Alter Titel', messages: [] }

    await chat.renameSession('session-1', 'Neuer Titel')

    expect(api.patch).toHaveBeenCalledWith('/sessions/session-1', { title: 'Neuer Titel' })
    expect(chat.sessions.map((session) => session.id)).toEqual(['session-1', 'session-2'])
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
      { id: 'session-2', pinned: false, updatedAt: '2026-07-20T14:00:00Z' },
      { id: 'session-1', pinned: false, updatedAt: '2026-07-20T12:00:00Z' }
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
})
