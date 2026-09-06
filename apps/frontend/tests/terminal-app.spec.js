// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Terminal } from '@xterm/xterm'
import TerminalApp from '../src/components/TerminalApp.vue'
import { TERMINAL_OPERATIONS } from '../src/control-center/manifest'
import { useTerminalStore } from '../src/stores/terminal'

vi.hoisted(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
})

vi.mock('stores/chat', () => ({ useChatStore: () => ({
  current: { id: 'session-1', preferredExecutionAgentId: 'agent-1', messages: [] },
  processOutputs: {}, activity: {}, stop: vi.fn()
}) }))
vi.mock('stores/control-center', () => ({ useControlCenterStore: () => ({}) }))
vi.mock('stores/execution-agents', () => ({ useExecutionAgentStore: () => ({
  byId: () => ({ connected: true, operations: TERMINAL_OPERATIONS })
}) }))
vi.mock('src/utils/notifications', () => ({ notifyError: vi.fn() }))

let app, store, xterm, writes
beforeEach(() => {
  setActivePinia(createPinia())
  store = useTerminalStore()
  store.$patch({ sessionId: 'session-1', agentId: 'agent-1', status: 'open' })
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
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function flushOutput() {
  await nextTick()
  await nextTick()
  await new Promise((resolve) => xterm.write('', resolve))
}

async function mountTerminal() {
  app = createApp(TerminalApp)
  for (const name of ['QIcon', 'QBtn', 'QBanner', 'QInput']) {
    app.component(name, { render() { return h('div', this.$slots.default?.()) } })
  }
  app.mount(document.createElement('div'))
  await flushOutput()
}

function lines(count) {
  return Array.from({ length: count }, (_, index) => (
    xterm.buffer.active.getLine(index).translateToString(true)
  ))
}

it.each(['auto', 'confirm'])('aligns attached %s LF output on initialization, streaming and replay', async (mode) => {
  store.$patch({ attached: true, mode, commandRunning: true })
  const output = Array.from({ length: 18 }, (_, index) => `line ${index}`)
  store.syncAgentOutput([], { tool: { stdout: `${output.join('\n')}\n`, stderr: '' } })
  await mountTerminal()
  expect(lines(19)).toEqual([...output, ''])

  store.syncAgentOutput([], { tool: { stdout: `${store.agentBuffer}\x1b[31mred\x1b[0m\r`, stderr: '' } })
  await flushOutput()
  store.syncAgentOutput([], { tool: { stdout: `${store.agentBuffer}\nnext\nold\rnew`, stderr: '' } })
  await flushOutput()
  expect(lines(21).slice(18)).toEqual(['red', 'next', 'new'])
  expect(xterm.buffer.active.getLine(18).getCell(0).getFgColor()).toBe(1)

  store.agentBuffer = 'replacement\nreplayed'
  await flushOutput()
  expect(lines(2)).toEqual(['replacement', 'replayed'])
  expect(writes.every(({ convertEol }) => convertEol)).toBe(true)
  expect(store.agentBuffer).toBe('replacement\nreplayed')
})

it('preserves personal PTY LF, CRLF, CR and ANSI cursor semantics', async () => {
  store.appendOutput('one\ntwo\r\nold\rnew\x1b[2DXY')
  await mountTerminal()
  expect(lines(3)).toEqual(['one', '   two', 'nXY'])
  store.appendOutput('\nend')
  await flushOutput()
  expect(lines(4)[3]).toBe('   end')
  store.personalBuffer = 'reset\nreplay'
  await flushOutput()
  expect(lines(2)).toEqual(['reset', '     replay'])
  expect(writes.every(({ convertEol }) => !convertEol)).toBe(true)
})

it('sets the view option before writes when attachment and output change in the same tick', async () => {
  store.personalBuffer = 'pty\nkept'
  await mountTerminal()

  writes.length = 0
  store.agentBuffer = 'agent\none'
  await store.receive(JSON.stringify({ type: 'session.attached', mode: 'auto' }))
  await flushOutput()
  expect(lines(2)).toEqual(['agent', 'one'])
  expect(writes.filter(({ data }) => data)).toEqual([
    { data: 'agent\none', convertEol: true }
  ])

  writes.length = 0
  store.appendOutput('\nnext')
  await store.receive(JSON.stringify({ type: 'session.detached' }))
  await flushOutput()
  expect(lines(3)).toEqual(['pty', '   kept', '       next'])
  expect(writes.filter(({ data }) => data)).toEqual([
    { data: 'pty\nkept\nnext', convertEol: false }
  ])

  await store.receive(JSON.stringify({ type: 'session.attached', mode: 'confirm' }))
  await flushOutput()
  expect(lines(2)).toEqual(['agent', 'one'])
  expect(xterm.options.convertEol).toBe(true)
})
