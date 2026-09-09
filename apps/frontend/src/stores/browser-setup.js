import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { useIntegrationStore } from 'stores/integrations'
import { browserSocketUrl, setupInputChunks } from 'src/utils/browser'
import { notifyError } from 'src/utils/notifications'

export const useBrowserSetupStore = defineStore('browser-setup', {
  state: () => ({ integrationId: null, socket: null, generation: 0, connected: false,
    status: null, starting: false, buffer: '', truncated: false, exitCode: null }),
  actions: {
    connect(integrationId) {
      this.release()
      this.integrationId = integrationId
      const generation = this.generation
      const socket = markRaw(new WebSocket(browserSocketUrl(integrationId, '/setup')))
      socket.binaryType = 'arraybuffer'
      this.socket = socket
      const decoder = new TextDecoder()
      const current = () => generation === this.generation && this.socket === socket
      socket.onopen = () => {
        if (!current()) return
        this.connected = true
        this.send({ type: 'status' })
      }
      socket.onmessage = (event) => {
        if (!current()) return
        if (event.data instanceof ArrayBuffer) {
          const next = this.buffer + decoder.decode(new Uint8Array(event.data), { stream: true })
          this.truncated ||= next.length > 512 * 1024
          this.buffer = next.slice(-512 * 1024)
          return
        }
        let message
        try { message = JSON.parse(event.data) } catch { return }
        if (message.type === 'state') {
          this.status = message
          this.starting = false
          useIntegrationStore().browserStatuses[integrationId] = message
        } else if (message.type === 'exit') {
          this.exitCode = message.exitCode
          this.starting = false
          this.send({ type: 'status' })
        } else if (message.type === 'error') {
          this.starting = false
          notifyError({ error: message.error })
        }
      }
      socket.onerror = () => { if (current()) notifyError({ error: { code: 'browser.unavailable' } }) }
      socket.onclose = () => {
        if (!current()) return
        this.connected = false
        this.starting = false
        this.socket = null
      }
    },
    send(message) {
      if (this.socket?.readyState !== 1) return false
      this.socket.send(JSON.stringify(message))
      return true
    },
    start() {
      if (this.starting || this.status?.state === 'installing') return
      if (this.send({ type: 'start' })) this.starting = true
    },
    input(text) {
      if (this.status?.state !== 'installing') return
      for (const data of setupInputChunks(text)) this.send({ type: 'input', data })
    },
    resize(columns, rows) {
      if (![columns, rows].every((value) => Number.isInteger(value) && value >= 1 && value <= 1000)) return
      this.send({ type: 'resize', columns, rows })
    },
    release() {
      const socket = this.socket
      const generation = this.generation + 1
      this.$reset()
      this.generation = generation
      socket?.close()
    }
  }
})
