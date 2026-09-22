// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Terminal } from '@xterm/xterm'
import TerminalApp from '../src/components/TerminalApp.vue'
import TerminalFooter from '../src/components/TerminalFooter.vue'
import { useTerminalStore } from '../src/stores/terminal'
import { useChatStore } from 'stores/chat'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useControlCenterStore } from 'stores/control-center'
import { TERMINAL_OPERATIONS } from '../src/control-center/manifest'
import { FakeWebSocket } from './terminal-websocket'

vi.hoisted(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
})

vi.mock('stores/chat', async () => {
  const { reactive } = await import('vue')
  const chat = reactive({ current: null, processOutputs: {}, activity: {}, running: false, stopping: false, stop: vi.fn() })
  return { useChatStore: () => chat }
})
vi.mock('stores/control-center', async () => {
  const { reactive } = await import('vue')
  const center = reactive({ ready: true, sessionId: null, tabs: [], confirmedState: { tabs: [] } })
  return { useControlCenterStore: () => center }
})
vi.mock('stores/execution-agents', async () => {
  const { reactive } = await import('vue')
  const { TERMINAL_OPERATIONS } = await import('../src/control-center/manifest')
  const agent = reactive({ connected: true, operations: TERMINAL_OPERATIONS })
  return { useExecutionAgentStore: () => ({ byId: (id = 'agent-1') => id === 'agent-1' ? agent : null }) }
})
vi.mock('src/utils/notifications', () => ({ notifyError: vi.fn() }))

let app, store, socket, xterm, writes, root
beforeEach(() => {
  setActivePinia(createPinia())
  store = useTerminalStore()
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  useChatStore().current = { id: 'session-1', preferredExecutionAgentId: 'agent-1', messages: [] }
  useChatStore().running = false
  useChatStore().stop.mockClear()
  Object.assign(useControlCenterStore(), {
    ready: true, sessionId: 'session-1', tabs: [{ appId: 'terminal' }], confirmedState: { tabs: [{ appId: 'terminal' }] }
  })
  Object.assign(useExecutionAgentStore().byId(), { connected: true, operations: TERMINAL_OPERATIONS })
  store.connect({ sessionId: 'session-1', agentId: 'agent-1' })
  socket = FakeWebSocket.instances.at(-1)
  socket.open()
  socket.snapshot()
  writes = []
  // Keep the real xterm parser and buffer; jsdom cannot measure or render a terminal.
  vi.spyOn(Terminal.prototype, 'open').mockImplementation(function () {
    xterm = this
    this.options.allowProposedApi = true
  })
  vi.spyOn(Terminal.prototype, 'loadAddon').mockImplementation(() => {})
  const write = Terminal.prototype.write
  vi.spyOn(Terminal.prototype, 'write').mockImplementation(function (data, callback) {
    writes.push({ data, convertEol: this.options.convertEol })
    return write.call(this, data, callback)
  })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
})
afterEach(() => {
  app?.unmount()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function flushOutput() {
  await nextTick()
  await nextTick()
  if (root?.querySelector('.terminal-app__host')) await new Promise((resolve) => xterm.write('', resolve))
}

async function mountTerminal() {
  app = createApp({ render: () => h('div', [h(TerminalApp), h(TerminalFooter)]) })
  for (const name of ['QIcon', 'QBanner', 'QInput', 'QTooltip', 'QSpinnerDots']) {
    app.component(name, { render() { return h('div', this.$slots.default?.()) } })
  }
  app.component('QToggle', {
    props: ['modelValue', 'disable', 'label'],
    emits: ['update:modelValue'],
    render() {
      return h('button', {
        role: 'switch',
        'aria-checked': String(this.modelValue),
        'aria-disabled': String(this.disable),
        disabled: this.disable,
        onClick: () => this.$emit('update:modelValue', !this.modelValue)
      }, this.label)
    }
  })
  app.component('QBtn', {
    props: ['label', 'disable'],
    render() { return h('button', { disabled: this.disable }, this.label) }
  })
  root = document.createElement('div')
  app.mount(root)
  await flushOutput()
}

function lines(count) {
  return Array.from({ length: count }, (_, index) => (
    xterm.buffer.active.getLine(index).translateToString(true)
  ))
}

it.each([false, true])('renders raw shared PTY output with attached=%s without duplicate chat output', async (attached) => {
  socket.state({ attached, busy: true, state: 'running' })
  const output = Array.from({ length: 18 }, (_, index) => `line ${index}`)
  socket.output(`${output.join('\r\n')}\r\n`)
  await mountTerminal()
  expect(lines(19)).toEqual([...output, ''])

  socket.output('\x1b[31mred\x1b[0m\r')
  await flushOutput()
  socket.output('\nnext\r\nold\rnew')
  await flushOutput()
  expect(lines(21).slice(18)).toEqual(['red', 'next', 'new'])
  expect(xterm.buffer.active.getLine(18).getCell(0).getFgColor()).toBe(1)

  useChatStore().processOutputs = { tool: { stdout: output.join('\r\n') } }
  await flushOutput()
  expect(lines(19)).toEqual([...output, 'red'])
  store.buffer = 'replacement\r\nreplayed'
  store.bufferRevision += 1
  await flushOutput()
  expect(lines(2)).toEqual(['replacement', 'replayed'])
  expect(writes.every(({ convertEol }) => !convertEol)).toBe(true)
  expect(store.buffer).toBe('replacement\r\nreplayed')
})

it('preserves personal PTY LF, CRLF, CR and ANSI cursor semantics', async () => {
  store.appendOutput('one\ntwo\r\nold\rnew\x1b[2DXY')
  await mountTerminal()
  expect(lines(3)).toEqual(['one', '   two', 'nXY'])
  store.appendOutput('\nend')
  await flushOutput()
  expect(lines(4)[3]).toBe('   end')
  store.buffer = 'reset\nreplay'
  store.bufferRevision += 1
  await flushOutput()
  expect(lines(2)).toEqual(['reset', '     replay'])
  expect(writes.every(({ convertEol }) => !convertEol)).toBe(true)
})

it('replays terminal queries without sending historical responses into the shell', async () => {
  store.buffer = 'prompt\x1b[6n'
  await mountTerminal()
  const inputs = () => socket.send.mock.calls
    .map(([data]) => JSON.parse(data)).filter((message) => message.type === 'input')
  expect(inputs()).toEqual([])
  store.buffer = 'replayed\x1b[6n'
  store.bufferRevision += 1
  await flushOutput()
  expect(inputs()).toEqual([])
  socket.output('\x1b[6n')
  await flushOutput()
  expect(inputs()).toHaveLength(1)
  expect(atob(inputs()[0].data)).toBe('\x1b[1;9R')
})

it('does not reset, redraw or switch buffers on attach and detach', async () => {
  store.buffer = 'pty\nkept'
  await mountTerminal()
  const reset = vi.spyOn(Terminal.prototype, 'reset')
  writes.length = 0
  socket.state({ attached: true })
  await flushOutput()
  expect(lines(2)).toEqual(['pty', '   kept'])
  expect(writes.filter(({ data }) => data)).toEqual([])
  store.appendOutput('\nnext')
  socket.state({ attached: false })
  await flushOutput()
  expect(lines(3)).toEqual(['pty', '   kept', '       next'])
  expect(writes.filter(({ data }) => data)).toEqual([
    { data: '\nnext', convertEol: false }
  ])
  expect(reset).not.toHaveBeenCalled()
  expect(xterm.options.convertEol).toBe(false)
})

it('keeps streaming across the bounded replay buffer without resetting the terminal', async () => {
  await mountTerminal()
  const reset = vi.spyOn(Terminal.prototype, 'reset')
  store.buffer = 'x'.repeat(512 * 1024)
  writes.length = 0
  store.appendOutput('live output')
  await flushOutput()
  expect(writes.filter(({ data }) => data)).toEqual([{ data: 'live output', convertEol: false }])
  expect(reset).not.toHaveBeenCalled()
  expect(store.truncated).toBe(false)
  expect(xterm.options.scrollback).toBe(50000)
})

it('reconnects A-B-A while mounted, replaces replay atomically and releases on unmount', async () => {
  socket.binary([65])
  await mountTerminal()
  expect(lines(1)).toEqual(['A'])
  for (const sessionId of ['session-2', 'session-1']) {
    useChatStore().current = { id: sessionId, preferredExecutionAgentId: 'agent-1', messages: [] }
    useControlCenterStore().sessionId = sessionId
    await nextTick()
    const next = FakeWebSocket.instances.at(-1)
    next.open()
    expect(store.ready).toBe(false)
    next.snapshot({
      attached: true, busy: true, state: 'running', personalData: btoa('resumed\r\n'),
      activeToolCallId: 'tool-1'
    })
    next.output('live\r\n')
    socket.binary([66])
    socket.onclose({ code: 4001 })
    await flushOutput()
    expect(lines(3)).toEqual(['resumed', 'live', ''])
    expect(store).toMatchObject({ sessionId, status: 'open', attached: true, runtimeState: 'running', busy: true })
    expect(xterm.options.disableStdin).toBe(true)
  }
  app.unmount()
  app = null
  for (const viewer of FakeWebSocket.instances) {
    expect(viewer.close).toHaveBeenCalledOnce()
    expect(viewer.send.mock.calls.some(([data]) => JSON.parse(data).type === 'close')).toBe(false)
  }
})

it.each([false, true])('restores output and the partial prompt after A-B-A with attached=%s', async (attached) => {
  const history = '$ ls\r\none.txt  two.txt\r\n$ cat \x1b[2D'
  socket.state({ attached })
  socket.output(history)
  await mountTerminal()
  const before = lines(3)
  const cursor = [xterm.buffer.active.cursorX, xterm.buffer.active.cursorY]
  for (const sessionId of ['session-2', 'session-1']) {
    useChatStore().current = { id: sessionId, preferredExecutionAgentId: 'agent-1', messages: [] }
    useControlCenterStore().sessionId = sessionId
    await nextTick()
    const viewer = FakeWebSocket.instances.at(-1)
    viewer.open()
    viewer.snapshot({ attached, personalData: btoa(sessionId === 'session-1' ? history : '$ other session') })
    await flushOutput()
  }
  expect(lines(3)).toEqual(before)
  expect([xterm.buffer.active.cursorX, xterm.buffer.active.cursorY]).toEqual(cursor)
})

it.each([
  ['no session', () => { useChatStore().current = null }, 'Es ist keine Session geöffnet.'],
  ['no agent selected', () => { useChatStore().current.preferredExecutionAgentId = null }, 'Für diese Session ist kein Agent ausgewählt.'],
  ['missing agent', () => { useChatStore().current.preferredExecutionAgentId = 'removed-agent' }, 'Der ausgewählte Agent ist nicht mehr verfügbar.'],
  ['offline agent', () => { useExecutionAgentStore().byId().connected = false }, 'Der ausgewählte Agent ist nicht erreichbar.'],
  ['unsupported agent', () => { useExecutionAgentStore().byId().operations = [] }, 'Der ausgewählte Agent unterstützt das Terminal nicht.']
])('shows one unavailable message with the reason for %s', async (_reason, configure, description) => {
  configure()
  await mountTerminal()
  const terminal = root.querySelector('.terminal-app')
  expect(terminal.querySelectorAll('.app-state-message')).toHaveLength(1)
  expect(terminal.querySelector('.text-subtitle1').textContent).toBe('Terminal nicht verfügbar')
  expect(terminal.querySelector('.text-body2').textContent).toBe(description)
  expect(terminal.querySelector('[name="terminal"]')).not.toBeNull()
  expect(terminal.querySelector('.terminal-app__host')).toBeNull()
})

it('releases on unavailability and reconnects when the agent returns', async () => {
  await mountTerminal()
  useExecutionAgentStore().byId().connected = false
  await nextTick()
  expect(socket.close).toHaveBeenCalledOnce()
  expect(store.socket).toBeNull()
  useExecutionAgentStore().byId().connected = true
  await nextTick()
  expect(FakeWebSocket.instances).toHaveLength(2)
  expect(store.status).toBe('connecting')
  expect(FakeWebSocket.instances.at(-1).url).toContain('resumeOnly=1&terminalId=pty-1')
})

it('automatically resumes a previously connected shell after viewer loss', async () => {
  await mountTerminal()
  vi.useFakeTimers()
  socket.onclose({ code: 1006 })
  await nextTick()
  expect(root.textContent).toContain('automatisch wiederhergestellt')
  expect(FakeWebSocket.instances).toHaveLength(1)
  vi.advanceTimersByTime(1000)
  await nextTick()
  const resumed = FakeWebSocket.instances.at(-1)
  expect(resumed.url).toContain('resumeOnly=1&terminalId=pty-1')
  resumed.open()
  resumed.snapshot({ attached: true, personalData: btoa('retained') })
  vi.useRealTimers()
  await flushOutput()
  expect(root.textContent).not.toContain('automatisch wiederhergestellt')
  expect(lines(1)).toEqual(['retained'])
  expect(store.attached).toBe(true)
})

it('stops retries after the server reports a terminated shell', async () => {
  await mountTerminal()
  vi.useFakeTimers()
  socket.onclose({ code: 1006 })
  await nextTick()
  vi.advanceTimersByTime(1000)
  await nextTick()
  const resumed = FakeWebSocket.instances.at(-1)
  resumed.open()
  resumed.message({ type: 'error', error: { code: 'terminal.disconnected' } })
  resumed.onclose({ code: 1000 })
  await nextTick()
  vi.advanceTimersByTime(60_000)
  expect(FakeWebSocket.instances).toHaveLength(2)
  expect(root.textContent).toContain('Neu verbinden')
  vi.useRealTimers()
})

it('cancels pending retries when the terminal view is removed', async () => {
  await mountTerminal()
  vi.useFakeTimers()
  socket.onclose({ code: 1006 })
  await nextTick()
  app.unmount()
  app = null
  vi.advanceTimersByTime(60_000)
  expect(FakeWebSocket.instances).toHaveLength(1)
  vi.useRealTimers()
})

it('shows replacement without reclaiming on remount or stale control actions', async () => {
  await mountTerminal()
  socket.message({ type: 'terminal.replaced' })
  socket.onclose({ code: 4001 })
  await nextTick()
  expect(root.textContent).toContain('In einer anderen Ansicht geöffnet')
  app.unmount()
  await mountTerminal()
  expect(root.textContent).toContain('In einer anderen Ansicht geöffnet')
  expect(FakeWebSocket.instances).toHaveLength(1)
})

it('keeps an idle terminal free of informational labels, including truncated replay', async () => {
  store.$patch({ restored: false, currentDirectory: '/terminal/cwd' })
  await mountTerminal()
  expect(root.textContent).not.toMatch(/Neue Shell gestartet|Terminal-Ordner|Shell bereit/)
  expect(root.querySelector('.terminal-app [role="status"]')).toBeNull()
  store.truncated = true
  await nextTick()
  expect(root.querySelector('.terminal-app [role="status"]')).toBeNull()
})

it('waits for persisted tab creation and resumes after a failed optimistic removal', async () => {
  store.release()
  const center = useControlCenterStore()
  center.confirmedState.tabs = []
  await mountTerminal()
  expect(FakeWebSocket.instances).toHaveLength(1)
  center.confirmedState.tabs = [{ appId: 'terminal' }]
  await nextTick()
  const viewer = FakeWebSocket.instances.at(-1)
  expect(FakeWebSocket.instances).toHaveLength(2)
  viewer.open()
  viewer.snapshot({ attached: true })
  await flushOutput()
  center.tabs = []
  await nextTick()
  expect(viewer.close).toHaveBeenCalledOnce()
  expect(viewer.send.mock.calls.some(([data]) => JSON.parse(data).type === 'close')).toBe(false)
  center.tabs = [{ appId: 'terminal' }]
  await nextTick()
  expect(FakeWebSocket.instances).toHaveLength(3)
  const resumed = FakeWebSocket.instances.at(-1)
  resumed.open()
  resumed.snapshot({ attached: true })
  await flushOutput()
  expect(store.attached).toBe(true)
})

it('keeps manual history and tab bytes raw, then disables input while agent-owned', async () => {
  useChatStore().running = true
  useChatStore().current.currentDirectory = '/session/workspace'
  socket.state({ attached: false, busy: false, state: 'idle', currentDirectory: '/terminal/cwd' })
  await mountTerminal()
  expect(xterm.options.disableStdin).toBe(false)
  socket.send.mockClear()
  xterm.input('\t\x1b[A', true)
  expect(socket.send.mock.calls.map(([data]) => JSON.parse(data))).toEqual([
    { type: 'input', data: btoa('\t\x1b[A') }
  ])
  socket.state({ attached: true, busy: true, state: 'running', activeToolCallId: 'tool-1' })
  await nextTick()
  expect(xterm.options.disableStdin).toBe(true)
  expect(root.textContent).toContain('Manuelle Eingaben sind gesperrt')
  expect(root.textContent).not.toMatch(/bearbeiten|geprüft|Strg\+C/)
  socket.send.mockClear()
  xterm.input('\r\x03', true)
  expect(socket.send).not.toHaveBeenCalled()
})

it('uses the attachment toggle for both directions and blocks it during an agent command', async () => {
  socket.state({ attached: true, busy: true, state: 'running', activeToolCallId: 'tool-1' })
  await mountTerminal()
  const toggle = root.querySelector('[role="switch"]')
  expect(root.textContent).toContain('Der Agent führt einen Befehl aus.')
  expect(toggle.getAttribute('aria-checked')).toBe('true')
  expect(toggle.getAttribute('aria-disabled')).toBe('true')
  expect(root.textContent).not.toContain('Manuell übernehmen')
  socket.state({ busy: false, state: 'idle', activeToolCallId: null })
  await nextTick()
  expect(root.querySelector('.terminal-app').nextElementSibling.textContent).not.toContain('Agent verbunden.')
  expect(toggle.getAttribute('aria-disabled')).not.toBe('true')
  toggle.click()
  expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toEqual({ type: 'detach' })
  expect(store.attached).toBe(true)
  await nextTick()
  expect(root.textContent).toContain('Manuelle Übernahme wird angefordert.')
  socket.state({ attached: false })
  await nextTick()
  expect(root.querySelector('.terminal-app').nextElementSibling.textContent).not.toContain('Manuelle Steuerung')
})
