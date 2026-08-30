import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { Notify } from 'quasar'
import { notifyError } from 'src/utils/notifications'
import {
  cumulativeOutputDelta,
  terminalCommandEntries
} from 'src/utils/terminal-output'

const MAX_BUFFER_LENGTH = 512 * 1024
const MAX_INPUT_BYTES = 65_536

function bounded(value, addition) {
  const next = value + addition
  return next.length > MAX_BUFFER_LENGTH ? next.slice(-MAX_BUFFER_LENGTH) : next
}

function websocketUrl(agentId, location = globalThis.location) {
  const protocol = location?.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location?.host}/api/terminals/${encodeURIComponent(agentId)}`
}

function encodedInputs(value) {
  const bytes = new TextEncoder().encode(value)
  const chunks = []
  let binary = ''
  for (const byte of bytes) {
    binary += String.fromCharCode(byte)
    if (binary.length === MAX_INPUT_BYTES) {
      chunks.push(btoa(binary))
      binary = ''
    }
  }
  if (binary) chunks.push(btoa(binary))
  return chunks
}

export const useTerminalStore = defineStore('terminal', {
  state: () => ({
    sessionId: null,
    agentId: null,
    ptySessionId: null,
    socket: null,
    decoder: null,
    generation: 0,
    status: 'closed',
    attached: false,
    mode: 'auto',
    personalBuffer: '',
    agentBuffer: '',
    proposal: null,
    submitting: null,
    requestedMode: null,
    submittedCommand: null,
    commandRunning: false,
    activeToolCallId: null,
    commandExit: null,
    currentDirectory: null,
    dimensions: { personal: null, agent: null },
    seenCommands: {},
    outputCursors: {},
    resizeRevision: 0,
    notifiedProposalId: null,
    errorNotified: false
  }),
  getters: {
    pendingProposal: (state) => Boolean(state.proposal),
    interactionLocked: (state) => Boolean(state.proposal || state.commandRunning || state.submitting),
    removalBlocked: (state) => Boolean(state.proposal || state.commandRunning || state.submitting)
  },
  actions: {
    connect({ sessionId, agentId }) {
      if (!sessionId || !agentId) return
      if (this.sessionId === sessionId && this.agentId === agentId && this.socket) return
      if (this.sessionId && (this.sessionId !== sessionId || this.agentId !== agentId)) this.close()
      const generation = ++this.generation
      const socket = new WebSocket(websocketUrl(agentId))
      socket.binaryType = 'arraybuffer'
      this.sessionId = sessionId
      this.agentId = agentId
      this.status = 'connecting'
      this.socket = markRaw(socket)
      this.decoder = markRaw(new TextDecoder())
      socket.onopen = () => {
        if (this.generation !== generation || this.socket !== socket) return
        this.status = 'open'
        this.errorNotified = false
      }
      socket.onmessage = (event) => void this.receive(event.data, generation, socket)
      socket.onerror = () => {
        if (this.generation !== generation || this.socket !== socket || this.errorNotified) return
        this.errorNotified = true
        notifyError({}, 'Die Terminalverbindung ist fehlgeschlagen.')
      }
      socket.onclose = () => {
        if (this.generation !== generation || this.socket !== socket) return
        this.flushDecoder()
        this.socket = null
        this.status = 'closed'
        this.attached = false
        this.submitting = null
        this.requestedMode = null
        this.submittedCommand = null
        this.commandRunning = false
        this.activeToolCallId = null
      }
    },
    async receive(raw, generation = this.generation, socket = this.socket) {
      if (generation !== this.generation || socket !== this.socket) return
      if (raw instanceof ArrayBuffer) {
        this.appendOutput(this.decoder?.decode(new Uint8Array(raw), { stream: true }) || '')
        return
      }
      if (typeof Blob !== 'undefined' && raw instanceof Blob) {
        const bytes = new Uint8Array(await raw.arrayBuffer())
        if (generation !== this.generation || socket !== this.socket) return
        this.appendOutput(this.decoder?.decode(bytes, { stream: true }) || '')
        return
      }
      if (typeof raw !== 'string') return
      let message
      try {
        message = JSON.parse(raw)
      } catch {
        notifyError({}, 'Der Computer hat eine ungültige Terminalnachricht gesendet.')
        return
      }
      if (message.type === 'terminal.opened') {
        this.ptySessionId = message.terminalId || this.ptySessionId
      } else if (message.type === 'session.attached') {
        this.attached = true
        this.proposal = null
        this.submitting = null
        if (['auto', 'confirm'].includes(message.mode)) this.mode = message.mode
        if (message.proposal) this.receiveProposal(message.proposal)
      } else if (message.type === 'session.detached') {
        this.attached = false
        this.submitting = null
      } else if (message.type === 'mode.changed') {
        if (['auto', 'confirm'].includes(message.mode)) {
          this.mode = message.mode
          this.requestedMode = null
          this.submitting = null
        }
      } else if (message.type === 'status') {
        this.ptySessionId = message.terminalSessionId || message.ptySessionId || this.ptySessionId
        this.currentDirectory = message.currentDirectory || message.directory || this.currentDirectory
      } else if (message.type === 'error') {
        this.errorNotified = true
        this.submitting = null
        this.requestedMode = null
        this.submittedCommand = null
        notifyError({ error: message.error }, 'Die Terminalaktion ist fehlgeschlagen.')
      } else if (message.type === 'command.proposed') {
        this.receiveProposal(message)
      } else if (message.type === 'command.resolved') {
        const decision = message.decision
        this.proposal = null
        if (decision !== 'execute') {
          this.submitting = null
          this.submittedCommand = null
          this.commandRunning = false
          this.commandExit = { decision: decision || 'deny', exitCode: null, timedOut: false }
        }
      } else if (message.type === 'command.started') {
        if (this.submittedCommand && this.activeToolCallId !== message.toolCallId) {
          this.appendAgentCommand(this.submittedCommand)
        }
        this.proposal = null
        this.submitting = null
        this.submittedCommand = null
        this.commandRunning = true
        this.activeToolCallId = message.toolCallId || null
        this.commandExit = null
        this.resizeRevision += 1
        const dimensions = this.dimensions.agent
        if (dimensions) this.send({
          type: 'resize',
          columns: dimensions.cols,
          rows: dimensions.rows
        })
      } else if (message.type === 'command.finished') {
        this.proposal = null
        this.submitting = null
        this.submittedCommand = null
        this.commandRunning = false
        this.activeToolCallId = null
        this.commandExit = {
          exitCode: Number.isInteger(message.exitCode) ? message.exitCode : null,
          timedOut: message.timedOut === true
        }
      } else if (['command.cancelled', 'command.canceled'].includes(message.type)) {
        this.proposal = null
        this.submitting = null
        this.submittedCommand = null
        this.commandRunning = false
        this.activeToolCallId = null
        this.commandExit = { decision: 'cancelled', exitCode: null, timedOut: false }
      }
    },
    flushDecoder() {
      if (!this.decoder) return
      this.appendOutput(this.decoder.decode())
      this.decoder = null
    },
    receiveProposal(message) {
      const requestId = message.approvalId || message.requestId || message.id
      if (!requestId || typeof message.command !== 'string') return
      this.proposal = { requestId, command: message.command }
      this.currentDirectory = message.currentDirectory || this.currentDirectory
      this.commandExit = null
      if (this.notifiedProposalId !== requestId) {
        this.notifiedProposalId = requestId
        Notify.create({
          type: 'warning',
          message: 'Im Terminal wartet ein Befehl auf Bestätigung.'
        })
      }
    },
    appendOutput(output) {
      if (!output) return
      this.personalBuffer = bounded(this.personalBuffer, output)
    },
    send(message) {
      if (this.socket?.readyState !== 1) return false
      this.socket.send(JSON.stringify(message))
      return true
    },
    attach(messages = [], processOutputs = {}) {
      if (!this.sessionId || this.attached || this.submitting || !this.send({
        type: 'attach',
        sessionId: this.sessionId,
        mode: this.mode
      })) return
      this.establishBaseline(messages, processOutputs)
      this.submitting = 'attach'
      this.commandExit = null
    },
    detach() {
      if (!this.attached || this.interactionLocked || !this.send({ type: 'detach' })) return
      this.submitting = 'detach'
    },
    setMode(mode) {
      if (!['auto', 'confirm'].includes(mode) || mode === this.mode || this.interactionLocked) return
      if (!this.send({ type: 'mode', mode })) return
      this.requestedMode = mode
      this.submitting = 'mode'
    },
    input(data) {
      if (this.attached) {
        if (this.mode === 'confirm' && this.commandRunning) {
          for (const encoded of encodedInputs(data)) this.send({ type: 'input', data: encoded })
        }
        return
      }
      for (const encoded of encodedInputs(data)) this.send({ type: 'input', data: encoded })
    },
    resize(view, cols, rows, force = false) {
      if (!Number.isInteger(cols) || !Number.isInteger(rows) || cols < 1 || rows < 1) return
      const previous = this.dimensions[view]
      if (!force && previous?.cols === cols && previous?.rows === rows) return
      this.dimensions[view] = { cols, rows }
      if ((view === 'agent' && this.attached) || (view === 'personal' && !this.attached)) {
        this.send({ type: 'resize', columns: cols, rows })
      }
    },
    resolveProposal(decision, command = this.proposal?.command) {
      const proposal = this.proposal
      if (!this.attached || !proposal || this.submitting || !['execute', 'deny'].includes(decision)) {
        return false
      }
      const sent = this.send(decision === 'execute'
        ? { type: 'command.execute', approvalId: proposal.requestId, command }
        : { type: 'command.deny', approvalId: proposal.requestId })
      if (!sent) return false
      this.submitting = decision
      this.submittedCommand = decision === 'execute' ? command : null
      return true
    },
    establishBaseline(messages, processOutputs) {
      this.seenCommands = Object.fromEntries(
        terminalCommandEntries(messages).map((entry) => [entry.id, true])
      )
      this.outputCursors = Object.fromEntries(Object.entries(processOutputs || {}).map(([id, output]) => [
        id,
        {
          stdout: typeof output?.stdout === 'string' ? output.stdout : '',
          stderr: typeof output?.stderr === 'string' ? output.stderr : ''
        }
      ]))
    },
    appendAgentCommand(command) {
      this.agentBuffer = bounded(this.agentBuffer, `\r\n\x1b[1;36m$ ${command}\x1b[0m\r\n`)
    },
    syncAgentOutput(messages, processOutputs) {
      if (!this.attached || (this.mode === 'confirm' && !this.commandRunning)) return
      if (this.mode === 'auto') {
        for (const entry of terminalCommandEntries(messages)) {
          if (this.seenCommands[entry.id]) continue
          this.seenCommands[entry.id] = true
          this.appendAgentCommand(entry.command)
        }
      }
      for (const [toolCallId, output] of Object.entries(processOutputs || {})) {
        const previous = this.outputCursors[toolCallId] || { stdout: '', stderr: '' }
        const stdout = typeof output?.stdout === 'string' ? output.stdout : ''
        const stderr = typeof output?.stderr === 'string' ? output.stderr : ''
        const delta = cumulativeOutputDelta(previous.stdout, stdout)
          + cumulativeOutputDelta(previous.stderr, stderr)
        if (delta) this.agentBuffer = bounded(this.agentBuffer, delta)
        this.outputCursors[toolCallId] = { stdout, stderr }
      }
    },
    syncContext({ sessionId, agentId, connected, supported, tabPresent, onChatRoute }) {
      if (!this.socket && !this.sessionId) return
      if (
        sessionId !== this.sessionId
        || agentId !== this.agentId
        || !connected
        || !supported
        || !tabPresent
        || !onChatRoute
      ) this.close()
    },
    close() {
      const socket = this.socket
      this.flushDecoder()
      this.generation += 1
      if (socket?.readyState === 1) {
        socket.send(JSON.stringify({ type: 'close' }))
        socket.close()
      } else if (socket) {
        socket.close()
      }
      this.$reset()
    }
  }
})
