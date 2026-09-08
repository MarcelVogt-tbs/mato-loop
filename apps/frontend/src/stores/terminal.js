import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { notifyError } from 'src/utils/notifications'

const MAX_BUFFER_LENGTH = 512 * 1024
const MAX_INPUT_BYTES = 65_536

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
    terminalId: null,
    socket: null,
    decoder: null,
    generation: 0,
    replacedContexts: [],
    status: 'closed',
    restored: null,
    attached: false,
    busy: false,
    runtimeState: 'closed',
    suspended: false,
    pending: null,
    activeToolCallId: null,
    currentDirectory: null,
    buffer: '',
    truncated: false,
    bufferRevision: 0,
    submitting: null,
    rows: null,
    columns: null,
    errorNotified: false
  }),
  getters: {
    ready: (state) => state.status === 'open',
    inputEnabled: (state) => state.status === 'open' && !state.suspended && state.runtimeState !== 'closed',
    removalBlocked: (state) => Boolean(state.busy || state.pending || state.submitting)
  },
  actions: {
    connect({ sessionId, agentId }) {
      if (!sessionId || !agentId) return
      if (this.sessionId === sessionId && this.agentId === agentId && this.socket
        && !['closed', 'resyncRequired'].includes(this.status)) return
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
      socket.onmessage = (event) => void this.receive(event.data, generation, socket)
      socket.onerror = () => {
        if (this.generation !== generation || this.socket !== socket || this.errorNotified) return
        this.errorNotified = true
        notifyError({}, 'Die Terminalverbindung ist fehlgeschlagen.')
      }
      socket.onclose = (event) => {
        if (this.generation !== generation || this.socket !== socket) return
        if (event?.code === 4001) this.markReplaced()
        else if (event?.code === 4002 && this.status !== 'replaced') this.status = 'resyncRequired'
        else if (!['replaced', 'resyncRequired'].includes(this.status)) this.status = 'closed'
        this.socket = null
        this.submitting = null
      }
    },
    async receive(raw, generation = this.generation, socket = this.socket) {
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
        this.truncated = message.personalTruncated === true
        this.buffer = this.decoder.decode(decodedBytes(message.personalData, this.truncated), { stream: true })
        this.applyStatus(message)
        this.rows = message.rows
        this.columns = message.columns
        this.bufferRevision += 1
        this.status = 'open'
      } else if (message.type === 'terminal.status') {
        if (this.ready && message.terminalId === this.terminalId) this.applyStatus(message)
      } else if (message.type === 'terminal.replaced') {
        this.markReplaced()
      } else if (message.type === 'terminal.closed') {
        if (this.status !== 'replaced') this.status = message.reason === 'resyncRequired' ? 'resyncRequired' : 'closed'
      } else if (message.type === 'error') {
        this.errorNotified = true
        this.submitting = null
        notifyError({ error: message.error }, 'Die Terminalaktion ist fehlgeschlagen.')
      }
    },
    applyStatus(message) {
      this.attached = message.attached === true
      this.busy = message.busy === true
      this.runtimeState = message.state
      this.suspended = message.suspended === true
      this.pending = message.pending ?? null
      this.activeToolCallId = message.activeToolCallId ?? null
      this.currentDirectory = message.currentDirectory ?? null
      if ((this.submitting === 'attach' && this.attached) || (this.submitting === 'detach' && !this.attached)) {
        this.submitting = null
      }
    },
    markReplaced() {
      this.status = 'replaced'
      const key = JSON.stringify([this.sessionId, this.agentId])
      if (!this.replacedContexts.includes(key)) this.replacedContexts.push(key)
    },
    appendOutput(output) {
      if (!output) return
      const next = this.buffer + output
      this.truncated ||= next.length > MAX_BUFFER_LENGTH
      this.buffer = next.slice(-MAX_BUFFER_LENGTH)
    },
    send(message) {
      if (!this.inputEnabled || this.socket?.readyState !== 1) return false
      this.socket.send(JSON.stringify(message))
      return true
    },
    attach() {
      if (!this.sessionId || this.attached || this.submitting || !this.send({
        type: 'attach', sessionId: this.sessionId
      })) return
      this.submitting = 'attach'
    },
    detach() {
      if (!this.attached || this.activeToolCallId || this.pending || this.submitting || !this.send({ type: 'detach' })) return
      this.submitting = 'detach'
    },
    input(data) {
      if (!this.inputEnabled) return
      for (const encoded of encodedInputs(data)) this.send({ type: 'input', data: encoded })
    },
    resize(columns, rows, force = false) {
      if (!Number.isInteger(columns) || !Number.isInteger(rows) || columns < 1 || rows < 1
        || columns > 1000 || rows > 1000) return
      if (!force && this.columns === columns && this.rows === rows) return
      if (this.send({ type: 'resize', columns, rows })) {
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
      const replacedContexts = this.replacedContexts
      this.$reset()
      this.generation = generation
      this.bufferRevision = bufferRevision
      this.replacedContexts = replacedContexts
      socket?.close()
    }
  }
})
