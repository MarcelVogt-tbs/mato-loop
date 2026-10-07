import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTerminalStore } from '../src/stores/terminal'
import { useControlCenterStore } from '../src/stores/control-center'
import { TERMINAL_OPERATIONS, terminalControlCenterState } from '../src/app/control-center/manifest'
import { FakeWebSocket } from './terminal-websocket'

const { api, notifyError } = vi.hoisted(() => ({ api: { patch: vi.fn() }, notifyError: vi.fn() }))
vi.mock('src/boot/axios', () => ({ api }))
vi.mock('src/utils/notifications', () => ({ notifyError }))

function connectedStore(state = {}) {
  const store = useTerminalStore()
  store.mode = 'terminal'
  store.connect({ sessionId: 'session-1', agentId: 'agent-1' })
  const socket = FakeWebSocket.instances.at(-1)
  socket.open()
  socket.snapshot(state)
  return { store, socket }
}

describe('terminal', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.clearAllMocks()
  })
  afterEach(() => vi.unstubAllGlobals())

  it('requires the personal terminal capabilities', () => {
    const agent = { connected: true, operations: TERMINAL_OPERATIONS }
    const context = { preferredExecutionAgentId: 'agent-1' }
    const agents = { byId: () => agent }
    expect(terminalControlCenterState(context, agents).available).toBe(true)
    expect(terminalControlCenterState({}, agents).reason).toBe('missing')
    agent.connected = false
    expect(terminalControlCenterState(context, agents).reason).toBe('offline')
    agent.connected = true
    agent.operations = ['terminal.open']
    expect(terminalControlCenterState(context, agents).reason).toBe('unsupported')
    expect(TERMINAL_OPERATIONS).toEqual([
      'terminal.open', 'terminal.read', 'terminal.write', 'terminal.resize', 'terminal.close'
    ])
  })

  it('waits for acquisition and snapshot before enabling controls', () => {
    const store = useTerminalStore()
    store.connect({ sessionId: 'session-1', agentId: 'agent-1' })
    const socket = FakeWebSocket.instances.at(-1)
    expect(socket.url).toContain('/api/terminals/agent-1?sessionId=session-1')
    expect(store.status).toBe('connecting')
    store.input('blocked')
    socket.open()
    expect(store.status).toBe('acquiring')
    socket.message({ type: 'terminal.opened', terminalId: 'pty-1', restored: true })
    expect(store.status).toBe('syncing')
    store.input('\r')
    expect(socket.send).not.toHaveBeenCalled()
    socket.snapshot({ busy: true, state: 'running', activeToolCallId: 'tool-1',
      currentDirectory: '/home/user/terminal', personalData: btoa('retained'), rows: 40, columns: 120 })
    expect(store).toMatchObject({ ready: true, restored: true, controlsEnabled: true, inputEnabled: false,
      runtimeState: 'running', activeToolCallId: 'tool-1',
      currentDirectory: '/home/user/terminal', buffer: 'retained', rows: 40, columns: 120 })
    expect(socket.send).not.toHaveBeenCalled()
    socket.output(' live')
    expect(store.buffer).toBe('retained live')
  })

  it.each(['idle', 'busy', 'running'])('forwards raw manual bytes in %s', (state) => {
    const { store, socket } = connectedStore({ busy: state !== 'idle', state })
    const input = 'ä\t\x1b[A\x7f\r\n\x03\x1b[200~paste\r\x1b[201~'
    store.input(input)
    const messages = socket.send.mock.calls.map(([data]) => JSON.parse(data))
    expect(messages.map((message) => message.type)).toEqual(['input'])
    expect(new TextDecoder().decode(Uint8Array.from(atob(messages[0].data), (char) => char.charCodeAt(0)))).toBe(input)
    expect(store.mode).toBe('terminal')
    expect(store.resolveProposal).toBeUndefined()
  })

  it.each([{ suspended: true }, { state: 'closed' }])('blocks input only for unavailable runtime state %j', (state) => {
    const { store, socket } = connectedStore(state)
    store.input('\r\x03')
    expect(socket.send).not.toHaveBeenCalled()
    socket.output('final output')
    expect(store.buffer).toBe('final output')
    socket.state({ suspended: false, state: 'idle' })
    store.input('x')
    expect(socket.send).toHaveBeenCalledOnce()
  })

  it('keeps agent replay and live output separate from the personal shell', () => {
    const { store, socket } = connectedStore({ personalData: btoa('personal'), agentData: btoa('stored') })
    store.mode = 'agent'
    socket.message({ type: 'terminal.agentOutput', data: btoa(' live') })
    socket.output(' shell')
    store.input('blocked')
    expect([store.agentBuffer, store.buffer, socket.send.mock.calls.length]).toEqual(['stored live', 'personal shell', 0])
  })

  it('reports controlled errors', () => {
    const { store, socket } = connectedStore()
    socket.message({ type: 'error', error: { code: 'terminal.unavailable' } })
    expect(store.ready).toBe(true)
    expect(notifyError).toHaveBeenCalledWith({ error: { code: 'terminal.unavailable' } }, expect.any(String))
  })

  it('resizes and retries dimensions that could not be sent while suspended', () => {
    const { store, socket } = connectedStore()
    store.resize(120, 40)
    store.resize(120, 40)
    expect(socket.send).toHaveBeenCalledExactlyOnceWith(JSON.stringify({ type: 'resize', columns: 120, rows: 40 }))
    socket.state({ suspended: true })
    store.resize(90, 30)
    socket.state({ suspended: false })
    store.resize(90, 30)
    expect(socket.send).toHaveBeenCalledTimes(2)
    store.resize(1001, 30)
    expect(socket.send).toHaveBeenCalledTimes(2)
  })

  it('decodes truncated snapshot boundaries and subsequent split UTF-8 frames', () => {
    const { store, socket } = connectedStore({ personalData: btoa('\x82\xacP\xe2\x82'), personalTruncated: true })
    socket.binary([0xac, 0xc3])
    socket.binary([0xa4])
    expect(store.buffer).toBe('P€ä')
    expect(store.truncated).toBe(true)
  })

  it('bounds replay data without reporting lost live scrollback', () => {
    const { store } = connectedStore()
    store.appendOutput('x'.repeat(512 * 1024 + 1))
    expect(store.buffer).toHaveLength(512 * 1024)
    expect(store.truncated).toBe(false)
  })

  it('chunks large multibyte input within the wire limit', () => {
    const { store, socket } = connectedStore()
    const input = `${'a'.repeat(65_535)}€`
    store.input(input)
    const chunks = socket.send.mock.calls.map(([value]) => atob(JSON.parse(value).data))
    expect(chunks).toHaveLength(2)
    expect(chunks.every((chunk) => chunk.length <= 65_536)).toBe(true)
    expect(new TextDecoder().decode(Uint8Array.from(chunks.join(''), (char) => char.charCodeAt(0)))).toBe(input)
  })

  it('replaces same-session replay after restart and ignores stale socket state and bytes', () => {
    const { store, socket } = connectedStore({ personalData: btoa('old') })
    socket.onclose({ code: 1006 })
    store.connect({ sessionId: 'session-1', agentId: 'agent-1' })
    const next = FakeWebSocket.instances.at(-1)
    next.open()
    next.snapshot({ terminalId: 'new-pty', personalData: btoa('new shell') }, false)
    socket.state({ currentDirectory: '/stale' })
    socket.output('stale')
    socket.onclose({ code: 4001 })
    expect(store).toMatchObject({ buffer: 'new shell', restored: false, terminalId: 'new-pty',
      currentDirectory: '/home/user', status: 'open' })
  })

  it.each(['message', 'close'])('does not reclaim replaced runtimes on stale actions or A-B-A navigation (%s)', (source) => {
    const { store, socket } = connectedStore()
    if (source === 'message') socket.message({ type: 'terminal.replaced' })
    else socket.onclose({ code: 4001 })
    store.input('x')
    socket.open()
    socket.snapshot()
    expect(store.status).toBe('replaced')
    store.connect({ sessionId: 'session-2', agentId: 'agent-1' })
    const second = FakeWebSocket.instances.at(-1)
    second.open()
    second.snapshot()
    store.release(socket)
    expect(store.socket).toBe(second)
    store.connect({ sessionId: 'session-1', agentId: 'agent-1' })
    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(store.status).toBe('replaced')
    expect(socket.send).not.toHaveBeenCalled()
  })

  it('offers manual resync without automatically retrying', () => {
    const { store, socket } = connectedStore()
    socket.message({ type: 'terminal.closed', reason: 'resyncRequired' })
    socket.onclose({ code: 4002 })
    expect(store.status).toBe('resyncRequired')
    expect(FakeWebSocket.instances).toHaveLength(1)
    store.connect({ sessionId: 'session-1', agentId: 'agent-1' })
    expect(store.status).toBe('connecting')
  })

  it.each(['onChatRoute', 'tabPresent', 'connected', 'supported'])('only releases on loss of %s', (field) => {
    const { store, socket } = connectedStore()
    store.syncContext({ sessionId: 'session-1', agentId: 'agent-1', onChatRoute: true,
      tabPresent: true, connected: true, supported: true, [field]: false })
    expect(socket.close).toHaveBeenCalledOnce()
    expect(socket.send).not.toHaveBeenCalled()
  })

  it('only releases the viewer on optimistic tab removal when persistence fails', async () => {
    const { store, socket } = connectedStore()
    const center = useControlCenterStore()
    center.syncSession({ id: 'session-1', controlCenter: { tabs: [{ appId: 'terminal' }] } })
    let reject
    api.patch.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail }))
    const removal = center.removeIntegration('terminal')
    store.syncContext({ sessionId: 'session-1', agentId: 'agent-1', connected: true, supported: true,
      tabPresent: center.tabs.some((tab) => tab.appId === 'terminal'), onChatRoute: true })
    expect(socket.close).toHaveBeenCalledOnce()
    reject(new Error('save failed'))
    await expect(removal).rejects.toThrow('save failed')
    expect(center.tabs.some((tab) => tab.appId === 'terminal')).toBe(true)
    expect(socket.send).not.toHaveBeenCalled()
  })
})
