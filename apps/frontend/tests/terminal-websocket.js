import { vi } from 'vitest'

export class FakeWebSocket {
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

  snapshot(state = {}, restored = true) {
    this.runtimeState = {
      terminalId: 'pty-1', attached: false, busy: false, state: 'idle', activeToolCallId: null,
      currentDirectory: '/home/user', suspended: false, ...state
    }
    this.message({ type: 'terminal.opened', restored, ...this.runtimeState })
    this.message({
      type: 'terminal.snapshot', terminalId: 'pty-1', personalData: '', personalTruncated: false,
      rows: 24, columns: 80, ...this.runtimeState
    })
  }

  state(state) {
    this.runtimeState = { ...this.runtimeState, ...state }
    this.message({ type: 'terminal.status', ...this.runtimeState })
  }

  output(data) {
    this.binary(new TextEncoder().encode(data))
  }
}
