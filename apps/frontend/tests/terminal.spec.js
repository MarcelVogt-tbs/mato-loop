import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { notifyCreate, notifyError } = vi.hoisted(() => ({
  notifyCreate: vi.fn(),
  notifyError: vi.fn()
}))

vi.mock('quasar', async (importOriginal) => ({
  ...await importOriginal(),
  Notify: { create: notifyCreate }
}))
vi.mock('src/utils/notifications', () => ({ notifyError }))

import {
  TERMINAL_OPERATIONS,
  terminalControlCenterState
} from '../src/control-center/manifest'
import { useTerminalStore } from '../src/stores/terminal'
import {
  cumulativeOutputDelta,
  handleAutoTerminalData,
  terminalProposalCommand,
  terminalExitStatus,
  terminalTabLabel
} from '../src/utils/terminal-output'

class FakeWebSocket {
  static instances = []

  constructor(url) {
    this.url = url
    this.readyState = 0
    this.send = vi.fn()
    this.close = vi.fn(() => { this.readyState = 3 })
    FakeWebSocket.instances.push(this)
  }

  open() {
    this.readyState = 1
    this.onopen?.()
  }

  message(value) {
    this.onmessage?.({ data: typeof value === 'string' ? value : JSON.stringify(value) })
  }

  binary(bytes) {
    this.onmessage?.({ data: Uint8Array.from(bytes).buffer })
  }
}

const agent = {
  id: 'agent-1',
  connected: true,
  operations: [...TERMINAL_OPERATIONS]
}
const oldMessages = [{
  toolCalls: [{ id: 'old-tool', name: 'shell_run', arguments: { command: 'printf old' } }]
}]
const oldOutputs = { 'old-tool': { stdout: 'old output', stderr: '' } }

function connectedStore() {
  const store = useTerminalStore()
  store.connect({ sessionId: 'session-1', agentId: 'agent-1' })
  const socket = FakeWebSocket.instances.at(-1)
  socket.open()
  return { store, socket }
}

function attach(store, socket, messages = [], outputs = {}) {
  store.attach(messages, outputs)
  socket.message({ type: 'session.attached', sessionId: 'session-1', mode: 'auto' })
}

function confirmMode(store, socket) {
  store.setMode('confirm')
  socket.message({ type: 'mode.changed', mode: 'confirm' })
}

describe('control center terminal', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    FakeWebSocket.instances = []
    globalThis.WebSocket = FakeWebSocket
    vi.clearAllMocks()
  })

  it('is launchable only for a selected connected agent with all terminal operations', () => {
    const agents = { byId: (id) => id === agent.id ? agent : null }

    expect(terminalControlCenterState({ preferredExecutionAgentId: agent.id }, agents).available).toBe(true)
    expect(terminalControlCenterState({}, agents).reason).toBe('missing')
    expect(terminalControlCenterState(
      { preferredExecutionAgentId: agent.id },
      { byId: () => ({ ...agent, connected: false }) }
    ).reason).toBe('offline')
    expect(terminalControlCenterState(
      { preferredExecutionAgentId: agent.id },
      { byId: () => ({ ...agent, operations: TERMINAL_OPERATIONS.slice(1) }) }
    ).reason).toBe('unsupported')
  })

  it('changes attachment and mode only after server acknowledgements', () => {
    const { store, socket } = connectedStore()

    store.attach(oldMessages, oldOutputs)
    expect(store.attached).toBe(false)
    expect(store.submitting).toBe('attach')
    socket.message({ type: 'session.attached', sessionId: 'session-1', mode: 'auto' })
    expect(store.attached).toBe(true)
    expect(store.submitting).toBeNull()

    store.setMode('confirm')
    expect(store.mode).toBe('auto')
    expect(store.submitting).toBe('mode')
    socket.message({ type: 'mode.changed', mode: 'confirm' })
    expect(store.mode).toBe('confirm')

    store.detach()
    expect(store.attached).toBe(true)
    expect(store.submitting).toBe('detach')
    socket.message({ type: 'session.detached', sessionId: 'session-1' })
    expect(store.attached).toBe(false)
    expect(store.submitting).toBeNull()
  })

  it('closes on context loss, supports controlled reconnect and ignores stale events', () => {
    const { store, socket: oldSocket } = connectedStore()
    oldSocket.message({ type: 'status', currentDirectory: '/home/one' })
    oldSocket.onclose()
    expect(store.status).toBe('closed')
    expect(store.sessionId).toBe('session-1')

    store.connect({ sessionId: 'session-1', agentId: 'agent-1' })
    const currentSocket = FakeWebSocket.instances.at(-1)
    currentSocket.open()
    oldSocket.message({ type: 'status', currentDirectory: '/stale' })
    expect(store.currentDirectory).toBe('/home/one')

    store.syncContext({
      sessionId: 'session-1', agentId: 'agent-1', connected: false,
      supported: false, tabPresent: true, onChatRoute: true
    })
    expect(store.status).toBe('closed')
    expect(store.sessionId).toBeNull()
    expect(currentSocket.close).toHaveBeenCalledOnce()
  })

  it('clears a proposal that was cancelled while disconnected on reattach', () => {
    const { store, socket: oldSocket } = connectedStore()
    attach(store, oldSocket)
    confirmMode(store, oldSocket)
    oldSocket.message({ type: 'command.proposed', approvalId: 'request-1', command: 'sleep 1' })
    oldSocket.onclose()

    store.connect({ sessionId: 'session-1', agentId: 'agent-1' })
    const socket = FakeWebSocket.instances.at(-1)
    socket.open()
    store.attach()
    socket.message({ type: 'session.attached', sessionId: 'session-1', mode: 'confirm' })

    expect(store.proposal).toBeNull()
    expect(store.removalBlocked).toBe(false)
  })

  it('restores an active proposal atomically on reattach', () => {
    const { store, socket } = connectedStore()
    store.attach()
    socket.message({
      type: 'session.attached',
      sessionId: 'session-1',
      mode: 'confirm',
      proposal: { type: 'command.proposed', approvalId: 'request-1', command: 'pwd' }
    })

    expect(store.proposal).toEqual({ requestId: 'request-1', command: 'pwd' })
    expect(store.removalBlocked).toBe(true)
  })

  it('decodes UTF-8 characters split across binary WebSocket frames', () => {
    const { store, socket } = connectedStore()

    socket.binary([0xe2, 0x82])
    expect(store.personalBuffer).toBe('')
    socket.binary([0xac])
    expect(store.personalBuffer).toBe('€')
  })

  it('chunks large multibyte terminal input within the wire limit', () => {
    const { store, socket } = connectedStore()
    const input = `${'a'.repeat(65_535)}€`

    store.input(input)

    const messages = socket.send.mock.calls.map(([value]) => JSON.parse(value))
    const bytes = messages.flatMap(({ data }) => [...Uint8Array.from(atob(data), (value) => value.charCodeAt(0))])
    expect(messages).toHaveLength(2)
    expect(messages.every(({ data }) => atob(data).length <= 65_536)).toBe(true)
    expect(new TextDecoder().decode(Uint8Array.from(bytes))).toBe(input)
  })

  it('starts Auto output at the attachment baseline and deduplicates cumulative deltas', () => {
    expect(cumulativeOutputDelta('one', 'one two')).toBe(' two')
    expect(cumulativeOutputDelta('one two', 'one')).toBe('')
    const { store, socket } = connectedStore()
    attach(store, socket, oldMessages, oldOutputs)

    store.syncAgentOutput(oldMessages, oldOutputs)
    expect(store.agentBuffer).toBe('')

    const messages = [...oldMessages, {
      toolCalls: [{ id: 'new-tool', name: 'shell_run', arguments: { command: 'printf new' } }]
    }]
    store.syncAgentOutput(messages, {
      ...oldOutputs,
      'new-tool': { stdout: 'new', stderr: '' }
    })
    store.syncAgentOutput(messages, {
      ...oldOutputs,
      'new-tool': { stdout: 'new output', stderr: '' }
    })

    expect(store.agentBuffer).not.toContain('printf old')
    expect(store.agentBuffer).not.toContain('old output')
    expect(store.agentBuffer.match(/\$ printf new/g)).toHaveLength(1)
    expect(store.agentBuffer).toContain('new output')
  })

  it('formats multiline agent commands as terminal prompt lines', () => {
    const store = useTerminalStore()

    store.appendAgentCommand('set -eu\nprintf one\rprintf two\r\npwd')

    expect(store.agentBuffer).toBe(
      '\r\n\x1b[1;36m$ set -eu\r\n> printf one\r\n> printf two\r\n> pwd\x1b[0m\r\n'
    )
  })

  it('retains proposals on errors and displays the final edited command after execute acknowledgement', () => {
    const { store, socket } = connectedStore()
    attach(store, socket)
    confirmMode(store, socket)
    socket.message({ type: 'command.proposed', approvalId: 'request-1', command: 'rm old' })
    socket.message({ type: 'command.proposed', approvalId: 'request-1', command: 'rm old' })

    expect(terminalProposalCommand(store.proposal)).toBe('rm old')
    expect(notifyCreate).toHaveBeenCalledOnce()
    expect(store.resolveProposal('execute', 'printf safe')).toBe(true)
    expect(store.proposal).not.toBeNull()
    expect(store.commandRunning).toBe(false)
    expect(store.submitting).toBe('execute')
    socket.message({ type: 'error', error: { code: 'terminal.proposalStale' } })
    expect(store.proposal).not.toBeNull()
    expect(store.submitting).toBeNull()

    store.resolveProposal('execute', 'printf final')
    socket.message({ type: 'command.resolved', approvalId: 'request-1', decision: 'execute' })
    expect(store.proposal).toBeNull()
    expect(store.commandRunning).toBe(false)
    socket.message({ type: 'command.started', toolCallId: 'tool-1' })
    expect(store.commandRunning).toBe(true)
    expect(store.agentBuffer).toContain('$ printf final')
    expect(store.agentBuffer).not.toContain('$ rm old')
    store.syncAgentOutput([], { 'tool-1': { stdout: 'result', stderr: '' } })
    expect(store.agentBuffer.indexOf('$ printf final')).toBeLessThan(store.agentBuffer.indexOf('result'))
    expect(store.resizeRevision).toBe(1)

    store.input('\x03')
    expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toEqual({ type: 'input', data: 'Aw==' })
  })

  it('keeps the proposal command available when the panel editor remounts', () => {
    const { store, socket } = connectedStore()
    attach(store, socket)
    confirmMode(store, socket)
    socket.message({ type: 'command.proposed', approvalId: 'request-1', command: 'printf remount' })

    const remountedEditorValue = terminalProposalCommand(store.proposal)
    expect(remountedEditorValue).toBe('printf remount')
    expect(store.pendingProposal).toBe(true)
  })

  it('clears denied and cancelled proposals only on server messages', () => {
    const { store, socket } = connectedStore()
    attach(store, socket)
    confirmMode(store, socket)
    socket.message({ type: 'command.proposed', approvalId: 'request-1', command: 'false' })
    store.resolveProposal('deny')
    expect(store.proposal).not.toBeNull()
    socket.message({ type: 'command.resolved', approvalId: 'request-1', decision: 'deny' })
    expect(store.proposal).toBeNull()
    expect(store.commandExit.decision).toBe('deny')

    socket.message({ type: 'command.proposed', approvalId: 'request-2', command: 'sleep 1' })
    socket.message({ type: 'command.cancelled', approvalId: 'request-2' })
    expect(store.proposal).toBeNull()
    expect(store.commandRunning).toBe(false)
    expect(store.commandExit.decision).toBe('cancelled')
  })

  it('caches and sends acknowledged attachment dimensions with backend field names', () => {
    const { store, socket } = connectedStore()
    attach(store, socket)

    store.resize('agent', 120, 40)
    expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toEqual({
      type: 'resize', columns: 120, rows: 40
    })
    store.resize('agent', 120, 40)
    expect(socket.send.mock.calls.filter(([value]) => JSON.parse(value).type === 'resize')).toHaveLength(1)
    store.resize('agent', 120, 40, true)
    expect(socket.send.mock.calls.filter(([value]) => JSON.parse(value).type === 'resize')).toHaveLength(2)
  })

  it('maps Auto Ctrl+C and exposes pending removal and accessible tab state', () => {
    const stop = vi.fn()
    expect(handleAutoTerminalData('x', stop)).toBe(false)
    expect(handleAutoTerminalData('\x03', stop)).toBe(true)
    expect(stop).toHaveBeenCalledOnce()

    const { store, socket } = connectedStore()
    attach(store, socket)
    confirmMode(store, socket)
    socket.message({ type: 'command.proposed', approvalId: 'request-1', command: 'true' })
    expect(store.removalBlocked).toBe(true)
    expect(terminalTabLabel('Terminal', store.pendingProposal))
      .toBe('Terminal, Befehl wartet auf Bestätigung')
  })

  it('describes timeout and signal completion from process result fields', () => {
    expect(terminalExitStatus({ timedOut: true, exitCode: -15 }))
      .toBe('Zeitlimit des Befehls überschritten.')
    expect(terminalExitStatus({ timedOut: false, exitCode: -15 }))
      .toBe('Befehl durch Signal 15 beendet.')
  })
})
