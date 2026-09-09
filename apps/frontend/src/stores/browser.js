import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { api } from 'src/boot/axios'
import { browserSocketUrl, clipboardFits } from 'src/utils/browser'
import { notifyError } from 'src/utils/notifications'

export const useBrowserStore = defineStore('browser', {
  state: () => ({
    sessionId: null, agentId: null, integrationId: null, browserId: null, viewId: null,
    offer: null, requestController: null, stoppedContext: null,
    socket: null, generation: 0, status: 'closed', attached: false, busy: false,
    canAttach: false, attachReason: null, submitting: null,
    rfb: null, viewHost: null, viewParent: null, viewGeneration: 0, viewStatus: 'closed',
    downloads: [], uploadId: null, transferring: false, scaleViewport: true,
    activationKey: null, activationAttempted: false, viewVisible: false,
    resizeTarget: null, resizeInFlight: null, resizeLast: null, resizeTimer: null,
    desktopSize: null, controlKeys: [], navigation: null, navigationPending: false
  }),
  getters: {
    ready: (state) => state.status === 'open' && state.viewStatus === 'open',
    manual: (state) => state.status === 'open' && state.viewStatus === 'open'
      && !state.attached && !state.busy && !state.submitting && !state.transferring && !state.navigationPending,
    canNavigate: (state) => state.manual && state.navigation !== null && state.socket?.readyState === 1,
    activeTab: (state) => state.navigation?.tabs.find((tab) => tab.id === state.navigation.activeTabId) ?? null
  },
  actions: {
    activate(key, context, eligible) {
      if (context && this.stoppedContext === JSON.stringify([context.integrationId, context.sessionId, context.agentId])) return
      if (key !== this.activationKey) {
        this.activationKey = key
        this.activationAttempted = false
      }
      if (!key || !eligible || this.activationAttempted) return
      this.activationAttempted = true
      this.connect(context)
    },
    connect({ integrationId, sessionId, agentId }) {
      if (!integrationId || !sessionId || !agentId) return
      if (!['closed', 'replaced'].includes(this.status) && this.integrationId === integrationId
        && this.sessionId === sessionId && this.agentId === agentId) return
      this.release()
      Object.assign(this, { integrationId, sessionId, agentId, status: 'connecting', stoppedContext: null })
      let generation = this.generation
      const socket = markRaw(new WebSocket(browserSocketUrl(integrationId, '', { sessionId })))
      this.socket = socket
      const current = () => this.generation === generation && this.socket === socket
      socket.onmessage = (event) => {
        if (!current() || typeof event.data !== 'string') return
        let message
        try { message = JSON.parse(event.data) } catch { return }
        if (message.type === 'availability' && ['occupied', 'waiting', 'takingOver'].includes(message.status)) {
          this.status = message.status
          this.offer = message.status === 'occupied' ? message : null
          this.updateViewOnly()
        } else if (message.type === 'replaced') {
          this.release({ replaced: true })
        } else if (message.type === 'error') {
          if (message.operation === 'navigation') {
            this.navigationPending = false
            this.updateViewOnly()
            notifyError({ error: message.error })
            return
          }
          if (message.operation === 'resize') {
            this.resizeInFlight = null
            this.scheduleResize()
            notifyError({ error: message.error })
            return
          }
          this.submitting = null
          this.updateViewOnly()
          if (!this.browserId || ['browser.bindingChanged', 'browser.notConnected'].includes(message.error?.code)) this.release()
          notifyError({ error: message.error })
        } else if (message.type === 'navigation') {
          if (!this.browserId || message.browserId !== this.browserId) return
          const { tabs, activeTabId, canGoBack, canGoForward, loading, nativeChrome } = message
          this.navigation = { tabs, activeTabId, canGoBack, canGoForward, loading, nativeChrome }
          // Unsolicited observations must not acknowledge an in-flight action.
          if (message.completed === true) this.navigationPending = false
          this.updateViewOnly()
        } else if (message.type === 'resized') {
          if (message.browserId !== this.browserId || !this.resizeInFlight) return
          this.desktopSize = { width: message.width, height: message.height }
          this.resizeInFlight = null
          this.scheduleResize()
        } else if (message.type === 'view.reset' && this.status === 'open' && this.browserId) {
          this.clearView('reset')
        } else if (message.type === 'state' && message.browserId && message.viewId) {
          if (this.browserId && this.browserId !== message.browserId) {
            this.release()
            notifyError({ error: { code: 'browser.bindingChanged' } })
            return
          }
          if (this.viewId && this.viewId !== message.viewId) {
            this.controlKeys = []
            generation = ++this.generation
            this.clearView()
            clearTimeout(this.resizeTimer)
            Object.assign(this, { navigation: null, navigationPending: false, downloads: [], uploadId: null,
              transferring: false, submitting: null, resizeInFlight: null, resizeLast: null, desktopSize: null })
          }
          this.browserId = message.browserId
          this.viewId = message.viewId
          this.offer = null
          this.status = 'open'
          this.attached = message.attached === true
          this.busy = message.busy === true
          this.canAttach = message.canAttach === true
          this.attachReason = message.attachReason ?? null
          if ((this.submitting === 'attach' && this.attached) || (this.submitting === 'detach' && !this.attached)) this.submitting = null
          this.updateViewOnly()
          if (this.viewStatus === 'reset' && message.attached === false && message.busy === false) {
            this.viewStatus = 'closed'
            void this.mountView(this.viewParent)
          }
        }
      }
      socket.onerror = () => {
        if (current()) notifyError({ error: { code: 'browser.unavailable' } })
      }
      socket.onclose = (event) => { if (current()) this.release({ replaced: event?.code === 4001 }) }
    },
    takeover() {
      if (this.status !== 'occupied' || !this.offer?.canTakeover || !this.offer.browserId
        || !this.offer.viewId || this.socket?.readyState !== 1) return
      const { browserId, viewId } = this.offer
      this.status = 'takingOver'
      this.socket.send(JSON.stringify({ type: 'takeover', browserId, viewId }))
    },
    clearView(status = 'closed') {
      // Invalidate callbacks and requests before disconnecting the old stream.
      this.viewGeneration += 1
      this.requestController?.abort()
      this.requestController = null
      this.transferring = false
      this.releaseControlKeys()
      this.viewStatus = status
      const rfb = this.rfb
      this.rfb = null
      if (rfb) rfb.viewOnly = true
      rfb?.disconnect()
      this.viewHost?.remove()
      this.viewHost = null
    },
    async mountView(host) {
      if (!host || this.status !== 'open' || !this.viewId) return
      this.viewParent = markRaw(host)
      if (this.viewStatus === 'reset') return
      if (this.viewHost) {
        // Keep the same viewer when the collapsed/mobile panel remounts.
        if (this.viewHost.parentElement !== host) host.replaceChildren(this.viewHost)
        return
      }
      if (this.viewStatus === 'connecting' && this.rfb === null) return
      const generation = this.generation
      const viewGeneration = ++this.viewGeneration
      this.viewStatus = 'connecting'
      try {
        const { default: RFB } = await import('@novnc/novnc/lib/rfb.js')
        if (generation !== this.generation || viewGeneration !== this.viewGeneration) return
        const viewHost = markRaw(document.createElement('div'))
        viewHost.className = 'fit'
        this.viewParent.replaceChildren(viewHost)
        this.viewHost = viewHost
        const rfb = markRaw(new RFB(viewHost, browserSocketUrl(this.integrationId, '/view', {
          sessionId: this.sessionId, browserId: this.browserId, viewId: this.viewId
        })))
        this.rfb = rfb
        rfb.scaleViewport = this.scaleViewport
        rfb.clipViewport = false
        rfb.resizeSession = false
        this.updateViewOnly()
        const current = () => generation === this.generation && viewGeneration === this.viewGeneration && this.rfb === rfb
        rfb.addEventListener('connect', () => {
          if (!current()) return
          this.viewStatus = 'open'
          this.updateViewOnly()
          this.navigate('state')
          const canvas = viewHost.querySelector('canvas')
          if (canvas) {
            canvas.tabIndex = 0
            canvas.setAttribute('aria-label', 'Entfernter Browser. Mit Strg+Alt+Escape zur lokalen Steuerung.')
          }
        })
        rfb.addEventListener('disconnect', () => {
          if (!current()) return
          this.release()
          notifyError({ error: { code: 'browser.notConnected' } })
        })
        for (const event of ['securityfailure', 'credentialsrequired']) {
          rfb.addEventListener(event, () => {
            if (!current()) return
            this.release()
            notifyError({ error: { code: 'browser.unavailable' } })
          })
        }
      } catch {
        if (generation !== this.generation || viewGeneration !== this.viewGeneration) return
        this.release()
        notifyError({ error: { code: 'browser.unavailable' } })
      }
    },
    toggleViewportScale() {
      this.scaleViewport = !this.scaleViewport
      if (this.rfb) this.rfb.scaleViewport = this.scaleViewport
    },
    updateViewOnly() {
      if (!this.manual) this.releaseControlKeys()
      if (this.rfb) this.rfb.viewOnly = !this.manual
    },
    controlKey(code, down) {
      if (!['ControlLeft', 'ControlRight'].includes(code)) return
      if (down && !this.manual) return
      if (!down && !this.controlKeys.includes(code)) return
      this.rfb?.sendKey(code === 'ControlRight' ? 0xffe4 : 0xffe3, code, down)
      this.controlKeys = this.controlKeys.filter((key) => key !== code)
      if (down) this.controlKeys.push(code)
    },
    releaseControlKeys() {
      for (const code of [...this.controlKeys]) this.controlKey(code, false)
    },
    clipboardChord(key) {
      if (!this.manual || !this.rfb || !['c', 'v'].includes(key)) return
      this.releaseControlKeys()
      const rfb = this.rfb
      const code = key === 'c' ? 'KeyC' : 'KeyV'
      try {
        rfb.sendKey(0xffe3, 'ControlLeft', true)
        rfb.sendKey(key.charCodeAt(0), code, true)
      } finally {
        rfb.sendKey(key.charCodeAt(0), code, false)
        rfb.sendKey(0xffe3, 'ControlLeft', false)
      }
    },
    devToolsChord() {
      if (!this.manual || !this.rfb || this.rfb.viewOnly) return
      this.releaseControlKeys()
      const rfb = this.rfb
      try {
        rfb.sendKey(0xffe3, 'ControlLeft', true)
        rfb.sendKey(0xffe1, 'ShiftLeft', true)
        rfb.sendKey(0x49, 'KeyI', true)
      } finally {
        rfb.sendKey(0x49, 'KeyI', false)
        rfb.sendKey(0xffe1, 'ShiftLeft', false)
        rfb.sendKey(0xffe3, 'ControlLeft', false)
      }
    },
    resize(width, height) {
      if (!this.viewVisible || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return
      this.resizeTarget = {
        width: Math.min(3840, Math.max(320, Math.floor(width))),
        height: Math.min(2160, Math.max(240, Math.floor(height)))
      }
      this.scheduleResize()
    },
    scheduleResize() {
      clearTimeout(this.resizeTimer)
      this.resizeTimer = null
      if (!this.resizeTarget || this.resizeInFlight) return
      this.resizeTimer = setTimeout(() => {
        this.resizeTimer = null
        const box = this.viewParent?.getClientRects()[0]
        if (!this.viewVisible || document.hidden || !(box?.width > 0 && box?.height > 0)
          || this.status !== 'open' || this.socket?.readyState !== 1 || this.resizeInFlight) return
        const target = this.resizeTarget
        if (!target || (target.width === this.resizeLast?.width && target.height === this.resizeLast?.height)) return
        // Remember failed targets too: only a new size or explicit retry may resend them.
        this.resizeLast = target
        this.resizeInFlight = target
        this.socket.send(JSON.stringify({ type: 'resize', ...target }))
      }, 200)
    },
    navigate(action, { tabId, url, nativeChrome } = {}) {
      if (this.status !== 'open' || this.socket?.readyState !== 1 || this.navigationPending) return false
      if (action !== 'state') {
        if (!this.canNavigate) return false
        if (['select', 'close', 'navigate', 'back', 'forward', 'reload', 'stop'].includes(action)) {
          tabId ??= this.navigation.activeTabId
          if (!this.navigation.tabs.some((tab) => tab.id === tabId)) return false
        }
        if (action === 'new' && this.navigation.tabs.length >= 32) return false
      }
      this.navigationPending = true
      this.updateViewOnly()
      this.socket.send(JSON.stringify({ type: 'navigation', action, tabId, url, nativeChrome }))
      return true
    },
    setAttachment(attached) {
      if (!this.ready || this.submitting || this.transferring || this.navigationPending
        || (attached && (this.busy || !this.canAttach || this.attached)) || (!attached && !this.attached)
        || this.socket?.readyState !== 1) return
      this.submitting = attached ? 'attach' : 'detach'
      this.updateViewOnly()
      this.socket.send(JSON.stringify({ type: this.submitting }))
    },
    copyClipboard() {
      return this.transferClipboard(undefined, { copy: true })
    },
    async request(callback) {
      this.requestController ??= markRaw(new AbortController())
      const { signal } = this.requestController
      const generation = this.generation
      const viewGeneration = this.viewGeneration
      const current = () => !signal.aborted && generation === this.generation && viewGeneration === this.viewGeneration
      try {
        const { data } = await callback({
          params: { browserId: this.browserId, sessionId: this.sessionId, viewId: this.viewId },
          signal, suppressErrorToast: true
        })
        return current() ? data : null
      } catch (error) {
        if (!current()) return null
        notifyError(error)
        throw error
      }
    },
    async transferClipboard(text, { copy = false } = {}) {
      if (!this.manual || (copy && text !== undefined) || (text !== undefined && !clipboardFits(text))) return null
      const generation = this.generation
      const viewGeneration = this.viewGeneration
      const path = `/browsers/${this.integrationId}/clipboard`
      this.transferring = true
      this.updateViewOnly()
      try {
        const data = await this.request((config) => copy
          ? api.post(`${path}/copy`, null, config)
          : text === undefined ? api.get(path, config) : api.put(path, { text }, config))
        if (!data || generation !== this.generation || viewGeneration !== this.viewGeneration
          || !this.ready || this.attached || this.busy || this.submitting) return null
        if (!clipboardFits(data.text)) {
          notifyError({}, 'Der Browsertext muss kleiner als 16 KiB sein. Bitte eine kleinere Auswahl kopieren.')
          return null
        }
        return data
      } finally {
        if (generation === this.generation && viewGeneration === this.viewGeneration) {
          this.transferring = false
          this.updateViewOnly()
        }
      }
    },
    async transfer(action, file = null) {
      if (!this.manual || (action === 'choose' && !this.uploadId)) return null
      const generation = this.generation
      const viewGeneration = this.viewGeneration
      const path = `/browsers/${this.integrationId}`
      this.transferring = true
      this.updateViewOnly()
      try {
        if (action === 'upload') {
          const form = new FormData()
          form.append('file', file)
          const data = await this.request((config) => api.post(`${path}/upload`, form, config))
          if (!data || generation !== this.generation || viewGeneration !== this.viewGeneration) return null
          this.uploadId = data.chosen ? null : data.uploadId
          return data
        }
        if (action === 'choose') {
          const data = await this.request((config) => api.post(`${path}/upload/${encodeURIComponent(this.uploadId)}/choose`, null, config))
          if (!data || generation !== this.generation || viewGeneration !== this.viewGeneration) return null
          if (data.ok) this.uploadId = null
          return data
        }
        const data = await this.request((config) => api.get(`${path}/downloads`, config))
        if (!data || generation !== this.generation || viewGeneration !== this.viewGeneration) return null
        this.downloads = data.downloads
        return data
      } finally {
        if (generation === this.generation && viewGeneration === this.viewGeneration) {
          this.transferring = false
          this.updateViewOnly()
        }
      }
    },
    async download(file) {
      if (!this.manual) return
      const generation = this.generation
      const viewGeneration = this.viewGeneration
      this.transferring = true
      this.updateViewOnly()
      try {
        const data = await this.request((config) => api.get(`/browsers/${this.integrationId}/download/${encodeURIComponent(file.id)}`, {
          ...config, responseType: 'blob'
        }))
        if (!data || generation !== this.generation || viewGeneration !== this.viewGeneration) return
        const url = URL.createObjectURL(data)
        const link = document.createElement('a')
        link.href = url
        link.download = file.filename
        link.click()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
      } finally {
        if (generation === this.generation && viewGeneration === this.viewGeneration) {
          this.transferring = false
          this.updateViewOnly()
        }
      }
    },
    syncContext({ sessionId, agentId, available, tabPresent, onChatRoute }) {
      if (this.sessionId && (this.sessionId !== sessionId || this.agentId !== agentId
        || !available || !tabPresent || !onChatRoute)) this.release()
    },
    release({ close = false, replaced = false } = {}) {
      const socket = this.socket
      const generation = this.generation + 1
      const { activationKey, activationAttempted } = this
      const context = { integrationId: this.integrationId, sessionId: this.sessionId, agentId: this.agentId }
      const stoppedContext = close || replaced ? JSON.stringify([this.integrationId, this.sessionId, this.agentId]) : this.stoppedContext
      // Replaced views must never send input or runtime-control messages.
      if (replaced) this.controlKeys = []
      this.generation = generation
      this.clearView()
      const viewGeneration = this.viewGeneration
      clearTimeout(this.resizeTimer)
      this.$reset()
      Object.assign(this, { activationKey, activationAttempted: close || replaced || activationAttempted, stoppedContext })
      if (replaced) Object.assign(this, context, { status: 'replaced' })
      this.generation = generation
      this.viewGeneration = viewGeneration
      if (socket?.readyState === 1 && !replaced) {
        if (close) socket.send(JSON.stringify({ type: 'close' }))
        socket.send(JSON.stringify({ type: 'release' }))
      }
      socket?.close()
    }
  }
})
