import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { encodedInputChunks } from 'src/utils/input-encoding'
import { notifyError } from 'src/utils/notifications'

const MAX_BUFFER_LENGTH = 512 * 1024

function websocketUrl(agentId, sessionId, location = globalThis.location) {
  const protocol = location?.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location?.host}/api/terminals/${encodeURIComponent(agentId)}?sessionId=${encodeURIComponent(sessionId)}`
}

function decodedBytes(data, truncated = false) {
  const bytes = Uint8Array.from(atob(data || ''), (value) => value.charCodeAt(0))
  // Replay may start inside a UTF-8 character after the server trims its byte buffer.
  let start = 0
  if (truncated) while (start < bytes.length && (bytes[start] & 0xc0) === 0x80) start += 1
  return bytes.subarray(start)
}

export const useTerminalStore = defineStore('terminal', {
  state: () => ({
    sessionId: null,
    agentId: null,
    terminalId: null,
    socket: null,
    decoder: null,
    agentDecoder: null,
    generation: 0,
    replacedContexts: [],
    status: 'closed',
    restored: null,
    mode: 'agent',
    runtimeState: 'closed',
    suspended: false,
    activeToolCallId: null,
    currentDirectory: null,
    buffer: '',
    truncated: false,
    bufferRevision: 0,
    output: '',
    outputRevision: 0,
    agentBuffer: '',
    agentTruncated: false,
    agentBufferRevision: 0,
    agentOutput: '',
    agentOutputRevision: 0,
    rows: null,
    columns: null,
    error: null,
    errorNotified: false
  }),
  getters: {
    ready: (state) => state.status === 'open',
    controlsEnabled: (state) => state.status === 'open' && !state.suspended && state.runtimeState !== 'closed',
    inputEnabled() {
      return this.controlsEnabled && this.mode === 'terminal'
    }
  },
  actions: {
    connect({ sessionId, agentId, automatic = false }) {
      if (!sessionId || !agentId) return
      if (automatic && this.status === 'error' && this.sessionId === sessionId && this.agentId === agentId) return
      if (this.sessionId === sessionId && this.agentId === agentId && this.socket
        && !['closed', 'resyncRequired', 'error'].includes(this.status)) return
      this.release()
      this.sessionId = sessionId
      this.agentId = agentId
      if (this.replacedContexts.includes(JSON.stringify([sessionId, agentId]))) {
        this.status = 'replaced'
        return
      }
      const generation = this.generation
      const socket = new WebSocket(websocketUrl(agentId, sessionId))
      socket.binaryType = 'arraybuffer'
      this.status = 'connecting'
      this.socket = markRaw(socket)
      socket.onopen = () => {
        if (this.generation !== generation || this.socket !== socket || this.status !== 'connecting') return
        this.status = 'acquiring'
        this.errorNotified = false
      }
      socket.onmessage = (event) => void this.receive(event.data, generation, socket, automatic)
      socket.onerror = () => {
        if (this.generation !== generation || this.socket !== socket || this.errorNotified) return
        this.errorNotified = true
        if (!automatic) notifyError({}, 'Die Terminalverbindung ist fehlgeschlagen.')
      }
      socket.onclose = (event) => {
        if (this.generation !== generation || this.socket !== socket) return
        if (event?.code === 4001) this.markReplaced()
        else if (event?.code === 4002 && !['replaced', 'error'].includes(this.status)) this.status = 'resyncRequired'
        else if (!['replaced', 'resyncRequired', 'error'].includes(this.status)) this.status = 'closed'
        this.socket = null
      }
    },
    async receive(raw, generation = this.generation, socket = this.socket, automatic = false) {
      if (generation !== this.generation || socket !== this.socket) return
      if (raw instanceof ArrayBuffer) {
        if (this.ready) this.appendOutput(this.decoder.decode(new Uint8Array(raw), { stream: true }))
        return
      }
      if (typeof Blob !== 'undefined' && raw instanceof Blob) {
        const bytes = new Uint8Array(await raw.arrayBuffer())
        if (generation !== this.generation || socket !== this.socket || !this.ready) return
        this.appendOutput(this.decoder.decode(bytes, { stream: true }))
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
        if (this.status !== 'acquiring') return
        this.terminalId = message.terminalId
        this.restored = message.restored === true
        this.applyStatus(message)
        this.status = 'syncing'
      } else if (message.type === 'terminal.snapshot') {
        if (this.status !== 'syncing' || message.terminalId !== this.terminalId) return
        this.decoder = markRaw(new TextDecoder())
        this.agentDecoder = markRaw(new TextDecoder())
        this.truncated = message.personalTruncated === true
        this.buffer = this.decoder.decode(decodedBytes(message.personalData, this.truncated), { stream: true })
        this.agentTruncated = message.agentTruncated === true
        this.agentBuffer = this.agentDecoder.decode(decodedBytes(message.agentData, this.agentTruncated), { stream: true })
        this.agentBufferRevision += 1
        this.applyStatus(message)
        this.rows = message.rows
        this.columns = message.columns
        this.bufferRevision += 1
        this.status = 'open'
      } else if (message.type === 'terminal.agentOutput') {
        if (this.ready) {
          const output = this.agentDecoder.decode(decodedBytes(message.data), { stream: true })
          this.agentBuffer = (this.agentBuffer + output).slice(-MAX_BUFFER_LENGTH)
          this.agentOutput = output
          this.agentOutputRevision += 1
        }
      } else if (message.type === 'terminal.status') {
        if (this.ready && message.terminalId === this.terminalId) this.applyStatus(message)
      } else if (message.type === 'terminal.replaced') {
        this.markReplaced()
      } else if (message.type === 'terminal.closed') {
        if (!['replaced', 'error'].includes(this.status)) this.status = message.reason === 'resyncRequired' ? 'resyncRequired' : 'closed'
      } else if (message.type === 'error') {
        this.errorNotified = true
        if (['executionAgent.protocolError', 'executionAgent.operationUnavailable'].includes(message.error?.code)) {
          this.error = message.error
          this.status = 'error'
          socket?.close()
        } else if (!automatic || this.ready) notifyError({ error: message.error }, 'Die Terminalaktion ist fehlgeschlagen.')
      }
    },
    applyStatus(message) {
      this.runtimeState = message.state
      this.suspended = message.suspended === true
      this.activeToolCallId = message.activeToolCallId ?? null
      this.currentDirectory = message.currentDirectory ?? null
    },
    markReplaced() {
      this.status = 'replaced'
      const key = JSON.stringify([this.sessionId, this.agentId])
      if (!this.replacedContexts.includes(key)) this.replacedContexts.push(key)
    },
    appendOutput(output) {
      if (!output) return
      this.buffer = (this.buffer + output).slice(-MAX_BUFFER_LENGTH)
      this.output = output
      this.outputRevision += 1
    },
    sendControl(message) {
      if (!this.controlsEnabled || this.socket?.readyState !== 1) return false
      this.socket.send(JSON.stringify(message))
      return true
    },
    input(data) {
      if (!this.inputEnabled) return
      for (const encoded of encodedInputChunks(data)) this.sendControl({ type: 'input', data: encoded })
    },
    resize(columns, rows, force = false) {
      if (!Number.isInteger(columns) || !Number.isInteger(rows) || columns < 1 || rows < 1
        || columns > 1000 || rows > 1000) return
      if (!force && this.columns === columns && this.rows === rows) return
      if (this.sendControl({ type: 'resize', columns, rows })) {
        this.columns = columns
        this.rows = rows
      }
    },
    syncContext({ sessionId, agentId, connected, supported, tabPresent, onChatRoute }) {
      if (!this.socket && !this.sessionId) return
      if (sessionId !== this.sessionId || agentId !== this.agentId || !connected
        || !supported || !tabPresent || !onChatRoute) this.release()
    },
    release(socket = this.socket) {
      if (socket !== this.socket) return
      const generation = this.generation + 1
      const bufferRevision = this.bufferRevision + 1
      const outputRevision = this.outputRevision + 1
      const replacedContexts = this.replacedContexts
      const mode = this.mode
      this.$reset()
      this.mode = mode
      this.generation = generation
      this.bufferRevision = bufferRevision
      this.outputRevision = outputRevision
      this.replacedContexts = replacedContexts
      socket?.close()
    }
  }
})
