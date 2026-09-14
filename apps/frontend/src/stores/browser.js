import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { Dark } from 'quasar'
import { api } from 'src/boot/axios'
import { browserSocketUrl, clipboardFits } from 'src/utils/browser'
import { parentPath } from 'src/features/workspace/state'
import { notifyError } from 'src/utils/notifications'
import { useControlCenterStore } from 'stores/control-center'

export const useBrowserStore = defineStore('browser', {
  state: () => ({
    sessionId: null, agentId: null, integrationId: null, browserId: null, viewId: null,
    displayedSessionId: null, displayGeneration: 0, candidate: null,
    authorizedRequest: null, requestRefresh: null, requestAttemptId: null, requestOpening: false,
    controlRequestId: null, requestedAttachSent: false, requestedAttachCancelled: false,
    offer: null, requestController: null, stoppedContext: null,
    socket: null, generation: 0, status: 'closed', attached: false, busy: false,
    canAttach: false, attachReason: null, submitting: null,
    rfb: null, viewHost: null, viewParent: null, viewGeneration: 0, viewStatus: 'closed',
    downloads: [], uploadId: null, transferring: false, scaleViewport: true,
    pickerScope: 'session', pickerDirectories: { session: '.', filesystem: '/' }, pickerEntries: [],
    pickerHasMore: false, pickerNextOffset: null, pickerSelected: null, pickerLoading: false,
    pickerError: null, pickerContext: null, pickerController: null, pickerGeneration: 0,
    activationKey: null, activationAttempted: false, viewVisible: false,
    resizeTarget: null, resizeInFlight: null, resizeLast: null, resizeTimer: null,
    desktopSize: null, controlKeys: [], navigation: null, navigationPending: false
  }),
  getters: {
    ownsDisplay: (state) => Boolean(state.sessionId && state.sessionId === state.displayedSessionId),
    displayStatus: (state) => state.candidate && state.candidate.sessionId === state.displayedSessionId ? state.candidate.status
      : state.ownsDisplay ? state.status : state.socket ? 'occupied' : 'closed',
    displayOffer: (state) => state.candidate && state.candidate.sessionId === state.displayedSessionId ? state.candidate.offer
      : state.ownsDisplay ? state.offer : null,
    displayNavigation: (state) => state.ownsDisplay && !state.candidate ? state.navigation : null,
    ready: (state) => state.ownsDisplay && !state.candidate && state.status === 'open' && state.viewStatus === 'open',
    manual: (state) => state.ready && state.viewVisible && !state.controlRequestId
      && !state.attached && !state.busy && !state.submitting && !state.transferring && !state.navigationPending,
    canNavigate: (state) => state.manual && state.navigation !== null && state.socket?.readyState === 1,
    activeTab: (state) => state.displayNavigation?.tabs.find((tab) => tab.id === state.navigation.activeTabId) ?? null
  },
  actions: {
    activate(key, context, eligible) {
      const request = this.authorizedRequest
      if (request && request.sessionId === context?.sessionId) {
        if (eligible && context.agentId === request.agentId && context.integrationId === request.integrationId
          && !this.requestOpening && !useControlCenterStore().saving && this.requestAttemptId !== request.id) {
          this.activationKey = key
          this.activationAttempted = true
          this.requestAttemptId = request.id
          this.connect(request, { controlRequestId: request.id })
        }
        return
      }
      if (this.requestRefresh) return
      if (context && this.stoppedContext === JSON.stringify([context.integrationId, context.sessionId, context.agentId])) return
      if (key !== this.activationKey) {
        this.activationKey = key
        this.activationAttempted = false
      }
      if (!key || !eligible || this.activationAttempted) return
      this.activationAttempted = true
      if (this.socket) return
      this.connect(context)
    },
    connect({ integrationId, sessionId, agentId }, { controlRequestId = null, takeover = false } = {}) {
      if (!integrationId || !sessionId || !agentId || sessionId !== this.displayedSessionId) return
      if (this.candidate?.sessionId === sessionId && this.candidate.controlRequestId === controlRequestId) return
      if (!['closed', 'replaced'].includes(this.status) && this.integrationId === integrationId
        && this.sessionId === sessionId && this.agentId === agentId && !controlRequestId) return
      this.cancelCandidate()
      const retained = Boolean(this.socket)
      if (retained) this.parkView()
      if (!retained) this.release({ preserveRequest: true })
      const binding = { integrationId, sessionId, agentId, controlRequestId, status: 'connecting', offer: null }
      let generation = this.generation
      const socket = markRaw(new WebSocket(browserSocketUrl(integrationId, '', {
        sessionId, ...(controlRequestId ? { controlRequestId } : {}), colorScheme: Dark.isActive ? 'dark' : 'light'
      })))
      if (retained) this.candidate = { ...binding, socket, takeover }
      else Object.assign(this, binding, { socket, stoppedContext: null })
      const current = () => this.candidate?.socket === socket || (this.generation === generation && this.socket === socket)
      socket.onmessage = (event) => {
        if (!current() || typeof event.data !== 'string') return
        let message
        try { message = JSON.parse(event.data) } catch { return }
        if (this.candidate?.socket === socket) {
          if (message.type === 'availability') {
            this.candidate.status = message.status
            this.candidate.offer = message.status === 'occupied' ? message : null
            if (this.candidate.takeover && message.status === 'occupied') {
              this.candidate.takeover = false
              this.takeover()
            }
            return
          }
          if (message.type === 'error' || message.type === 'replaced') {
            this.cancelCandidate()
            if (message.type === 'error') notifyError({ error: message.error })
            return
          }
          if (message.type !== 'state' || !message.browserId || !message.viewId) return
          // Only an accepted binding can retire the retained owner and its live viewer.
          this.candidate = null
          this.release({ replaced: true, preserveRequest: true })
          generation = this.generation
          Object.assign(this, binding, { socket, browserId: null, stoppedContext: null })
        }
        if (message.type === 'availability' && ['occupied', 'waiting', 'takingOver'].includes(message.status)) {
          this.status = message.status
          this.offer = message.status === 'occupied' ? message : null
          this.updateViewOnly()
        } else if (message.type === 'replaced') {
          this.release({ replaced: true, preserveRequest: true, preserveCandidate: true })
        } else if (message.type === 'error') {
          if (message.operation === 'control') {
            this.controlRequestId = null
            if (this.authorizedRequest?.id === controlRequestId) this.authorizedRequest = null
            if (this.submitting === 'attach') this.submitting = null
            this.updateViewOnly()
            this.scheduleResize()
            if (!this.requestedAttachCancelled) notifyError({ error: message.error })
            if (this.viewStatus === 'open' && !this.navigation) this.navigate('state')
            return
          }
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
          this.controlRequestId = null
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
            if (this.rfb) this.rfb.viewOnly = true
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
          if (this.controlRequestId && !this.requestedAttachSent && this.socket?.readyState === 1) {
            this.requestedAttachSent = true
            this.submitting = 'attach'
            this.socket.send(JSON.stringify({ type: 'attach.requested', requestId: this.controlRequestId }))
          }
          const requestedAttached = this.controlRequestId && this.requestedAttachSent && message.attached === true
          const requestedDetached = this.requestedAttachCancelled && message.attached === false && message.busy !== true
          this.attached = message.attached === true
          if (this.attached && this.requestedAttachSent) this.controlRequestId = null
          this.busy = message.busy === true
          this.canAttach = message.canAttach === true
          this.attachReason = message.attachReason ?? null
          if ((this.submitting === 'attach' && this.attached) || (this.submitting === 'detach' && !this.attached)) this.submitting = null
          this.updateViewOnly()
          if ((requestedAttached || requestedDetached) && this.viewStatus === 'open' && !this.navigation) this.navigate('state')
          this.scheduleResize()
          if (this.viewStatus === 'reset' && message.attached === false && message.busy === false) {
            this.viewStatus = 'closed'
            void this.mountView(this.viewParent)
          }
        }
      }
      socket.onerror = () => {
        if (!current()) return
        if (this.candidate?.socket === socket) this.cancelCandidate()
        notifyError({ error: { code: 'browser.unavailable' } })
      }
      socket.onclose = (event) => {
        if (!current()) return
        if (this.candidate?.socket === socket) this.cancelCandidate()
        else this.release({ replaced: event?.code === 4001, preserveRequest: true, preserveCandidate: true })
      }
    },
    takeover() {
      const target = this.candidate || (this.ownsDisplay ? this : null)
      if (!target || target.sessionId !== this.displayedSessionId || target.status !== 'occupied'
        || !target.offer?.canTakeover || !target.offer.browserId || !target.offer.viewId || target.socket?.readyState !== 1) return
      const { browserId, viewId } = target.offer
      target.status = 'takingOver'
      target.socket.send(JSON.stringify({ type: 'takeover', browserId, viewId }))
    },
    cancelCandidate() {
      const candidate = this.candidate
      this.candidate = null
      candidate?.socket.close()
    },
    setDisplayedSession(sessionId) {
      sessionId ||= null
      if (sessionId === this.displayedSessionId) return
      this.parkView()
      this.displayedSessionId = sessionId
      this.requestRefresh?.controller.abort()
      this.requestRefresh = null
      this.authorizedRequest = null
      this.requestOpening = false
      this.cancelCandidate()
      // A claimed request must never attach later merely because the chat was reopened.
      if (this.controlRequestId && !this.requestedAttachSent) {
        this.requestedAttachCancelled = true
        if (this.socket?.readyState === 1) this.socket.send(JSON.stringify({ type: 'detach' }))
        this.controlRequestId = null
      }
    },
    parkView() {
      this.displayGeneration += 1
      this.viewVisible = false
      this.releaseControlKeys()
      this.updateViewOnly()
      this.rfb?.blur?.()
      this.viewHost?.remove()
      this.requestController?.abort()
      this.requestController = null
      this.resetPicker()
      this.transferring = false
    },
    async reconcileControlRequest(sessionId) {
      const center = useControlCenterStore()
      if (!sessionId || this.displayedSessionId !== sessionId || center.sessionId !== sessionId || !center.ready) return
      if (this.requestRefresh) { this.requestRefresh.dirty = true; return }
      const refresh = markRaw({ controller: new AbortController(), dirty: false })
      this.requestRefresh = refresh
      const current = () => !refresh.controller.signal.aborted && this.displayedSessionId === sessionId
        && center.sessionId === sessionId && center.ready
      try {
        do {
          refresh.dirty = false
          const { data } = await api.get('/browsers/control-requests', {
            params: { sessionId }, signal: refresh.controller.signal, suppressErrorToast: true
          })
          if (!current()) return
          const request = data.request
          if (!request) {
            this.authorizedRequest = null
            if (this.candidate?.controlRequestId) this.cancelCandidate()
            if (!this.requestedAttachSent) this.controlRequestId = null
            continue
          }
          if (request.sessionId !== sessionId || !request.id || !request.integrationId || !request.agentId || !request.runId) return
          if (this.authorizedRequest?.id === request.id || this.requestAttemptId === request.id) continue
          this.authorizedRequest = request
          this.requestOpening = true
          try {
            await center.ensureApp('browser')
            if (current()) {
              this.requestAttemptId = request.id
              this.connect(request, { controlRequestId: request.id })
            }
          } catch {
            if (current()) { this.requestAttemptId = request.id; this.authorizedRequest = null }
          } finally {
            if (current()) this.requestOpening = false
          }
        } while (refresh.dirty && current())
      } catch (error) {
        if (current()) notifyError(error)
      } finally {
        if (this.requestRefresh === refresh) this.requestRefresh = null
      }
    },
    clearView(status = 'closed') {
      // Invalidate callbacks and requests before disconnecting the old stream.
      this.viewGeneration += 1
      this.requestController?.abort()
      this.requestController = null
      this.resetPicker()
      this.transferring = false
      this.releaseControlKeys()
      this.viewStatus = status
      this.updateViewOnly()
      const rfb = this.rfb
      this.rfb = null
      if (rfb) rfb.viewOnly = true
      rfb?.disconnect()
      this.viewHost?.remove()
      this.viewHost = null
    },
    async mountView(host) {
      if (!host || !this.ownsDisplay || this.candidate || this.status !== 'open' || !this.viewId) return
      const restoring = !this.viewVisible || this.viewHost?.parentElement !== host
      this.viewVisible = true
      this.updateViewOnly()
      this.viewParent = markRaw(host)
      if (this.viewStatus === 'reset') return
      if (this.viewHost) {
        // Keep the same viewer when the collapsed/mobile panel remounts.
        if (this.viewHost.parentElement !== host) host.replaceChildren(this.viewHost)
        if (restoring && this.rfb) this.rfb.scaleViewport = this.scaleViewport
        if (this.viewStatus === 'open' && !this.navigation) this.navigate('state')
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
        if (this.viewVisible && this.ownsDisplay) this.viewParent.replaceChildren(viewHost)
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
        for (const event of ['disconnect', 'securityfailure', 'credentialsrequired']) {
          rfb.addEventListener(event, () => {
            if (!current()) return
            // The old relay can end before the control socket announces the handoff.
            if (this.candidate) { this.clearView(); return }
            this.release()
            notifyError({ error: { code: event === 'disconnect' ? 'browser.notConnected' : 'browser.unavailable' } })
          })
        }
      } catch {
        if (generation !== this.generation || viewGeneration !== this.viewGeneration) return
        if (this.candidate) { this.clearView(); return }
        this.release()
        notifyError({ error: { code: 'browser.unavailable' } })
      }
    },
    toggleViewportScale() {
      if (!this.ownsDisplay) return
      this.scaleViewport = !this.scaleViewport
      if (this.rfb) this.rfb.scaleViewport = this.scaleViewport
    },
    updateViewOnly() {
      if (!this.manual) {
        this.releaseControlKeys()
        // noVNC 1.5 sets viewOnly before ungrab, which suppresses its own key-up events.
        if (this.rfb && !this.rfb.viewOnly) this.rfb._keyboard?.ungrab()
      }
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
      if (!this.ownsDisplay || !this.viewVisible || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return
      this.resizeTarget = {
        width: Math.min(3840, Math.max(320, Math.floor(width))),
        height: Math.min(2160, Math.max(240, Math.floor(height)))
      }
      this.scheduleResize()
    },
    scheduleResize() {
      clearTimeout(this.resizeTimer)
      this.resizeTimer = null
      if (!this.resizeTarget || this.resizeInFlight || this.controlRequestId || this.submitting) return
      this.resizeTimer = setTimeout(() => {
        this.resizeTimer = null
        const box = this.viewParent?.getClientRects()[0]
        if (!this.ownsDisplay || !this.viewVisible || document.hidden || !(box?.width > 0 && box?.height > 0)
          || this.status !== 'open' || this.socket?.readyState !== 1 || this.resizeInFlight
          || this.controlRequestId || this.submitting) return
        const target = this.resizeTarget
        if (!target || (target.width === this.resizeLast?.width && target.height === this.resizeLast?.height)) return
        // Remember failed targets too: only a new size or explicit retry may resend them.
        this.resizeLast = target
        this.resizeInFlight = target
        this.socket.send(JSON.stringify({ type: 'resize', ...target }))
      }, 200)
    },
    navigate(action, { tabId, url, nativeChrome } = {}) {
      if (!this.ownsDisplay || this.status !== 'open' || this.socket?.readyState !== 1 || this.navigationPending
        || this.controlRequestId || this.submitting === 'detach') return false
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
      if (!this.ownsDisplay) return
      if (!attached && this.controlRequestId && this.socket?.readyState === 1) {
        this.requestedAttachCancelled = true
        this.controlRequestId = null
        this.authorizedRequest = null
        this.submitting = 'detach'
        this.socket.send(JSON.stringify({ type: 'detach' }))
        return
      }
      if (this.controlRequestId) return
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
      if (!this.ownsDisplay) return null
      this.requestController ??= markRaw(new AbortController())
      const { signal } = this.requestController
      const generation = this.generation
      const viewGeneration = this.viewGeneration
      const displayGeneration = this.displayGeneration
      const current = () => !signal.aborted && generation === this.generation && viewGeneration === this.viewGeneration
        && displayGeneration === this.displayGeneration
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
      const displayGeneration = this.displayGeneration
      const path = `/browsers/${this.integrationId}/clipboard`
      this.transferring = true
      this.updateViewOnly()
      try {
        const data = await this.request((config) => copy
          ? api.post(`${path}/copy`, null, config)
          : text === undefined ? api.get(path, config) : api.put(path, { text }, config))
        if (!data || generation !== this.generation || viewGeneration !== this.viewGeneration || displayGeneration !== this.displayGeneration
          || !this.ready || this.attached || this.busy || this.submitting) return null
        if (!clipboardFits(data.text)) {
          notifyError({}, 'Der Browsertext muss kleiner als 16 KiB sein. Bitte eine kleinere Auswahl kopieren.')
          return null
        }
        return data
      } finally {
        if (generation === this.generation && viewGeneration === this.viewGeneration && displayGeneration === this.displayGeneration) {
          this.transferring = false
          this.updateViewOnly()
        }
      }
    },
    async transfer(action) {
      if (!this.manual || (action === 'choose' && !this.uploadId)) return null
      const generation = this.generation
      const viewGeneration = this.viewGeneration
      const displayGeneration = this.displayGeneration
      const path = `/browsers/${this.integrationId}`
      this.transferring = true
      this.updateViewOnly()
      try {
        if (action === 'choose') {
          const data = await this.request((config) => api.post(`${path}/upload/${encodeURIComponent(this.uploadId)}/choose`, null, config))
          if (!data || generation !== this.generation || viewGeneration !== this.viewGeneration || displayGeneration !== this.displayGeneration) return null
          if (data.ok) this.uploadId = null
          return data
        }
        const data = await this.request((config) => api.get(`${path}/downloads`, config))
        if (!data || generation !== this.generation || viewGeneration !== this.viewGeneration || displayGeneration !== this.displayGeneration) return null
        this.downloads = data.downloads
        return data
      } finally {
        if (generation === this.generation && viewGeneration === this.viewGeneration && displayGeneration === this.displayGeneration) {
          this.transferring = false
          this.updateViewOnly()
        }
      }
    },
    resetPicker() {
      this.pickerController?.abort()
      this.pickerController = null
      this.pickerGeneration += 1
      this.pickerEntries = []
      this.pickerHasMore = false
      this.pickerNextOffset = null
      this.pickerSelected = null
      this.pickerLoading = false
      this.pickerError = null
      this.pickerContext = null
    },
    openPicker(context) {
      if (!this.manual || context?.sessionId !== this.sessionId) return false
      this.resetPicker()
      this.pickerContext = { ...context, currentDirectory: context.currentDirectory || context.homeDirectory || '/' }
      this.pickerDirectories = {
        session: '.',
        filesystem: context.currentDirectory || context.homeDirectory || '/'
      }
      this.pickerScope = 'session'
      void this.loadPicker()
      return true
    },
    pickerParams(extra = {}, read = true) {
      const filesystem = this.pickerScope === 'filesystem'
      return {
        scope: this.pickerScope,
        session_id: this.sessionId,
        ...(filesystem ? {
          execution_agent_id: this.pickerContext.executionAgentId,
          current_directory: this.pickerContext.currentDirectory
        } : read && this.pickerContext.rootIdentity ? { root_identity: this.pickerContext.rootIdentity } : {}),
        ...extra
      }
    },
    async setPickerScope(scope) {
      if (!this.manual || !['session', 'filesystem'].includes(scope) || !this.pickerContext
        || (scope === 'filesystem' && !this.pickerContext.filesystemList)) return false
      this.pickerScope = scope
      return this.loadPicker()
    },
    openPickerDirectory(path) {
      if (typeof path !== 'string' || (this.pickerScope === 'filesystem' && !path.startsWith('/'))) return false
      this.pickerDirectories[this.pickerScope] = path
      return this.loadPicker()
    },
    pickerParent() {
      return parentPath(this.pickerDirectories[this.pickerScope])
    },
    async loadPicker(append = false) {
      if (!this.manual || !this.pickerContext || (this.pickerScope === 'filesystem' && !this.pickerContext.filesystemList)) return false
      this.pickerController?.abort()
      const controller = markRaw(new AbortController())
      this.pickerController = controller
      const generation = ++this.pickerGeneration
      const browserGeneration = this.generation
      const viewGeneration = this.viewGeneration
      const displayGeneration = this.displayGeneration
      const directory = this.pickerDirectories[this.pickerScope]
      this.pickerLoading = true
      this.pickerError = null
      try {
        const { data } = await api.get('/workspace/entries', {
          params: this.pickerParams({ path: directory, ...(append ? { offset: this.pickerNextOffset } : {}) }),
          signal: controller.signal,
          suppressErrorToast: true
        })
        if (controller.signal.aborted || generation !== this.pickerGeneration || browserGeneration !== this.generation
          || viewGeneration !== this.viewGeneration || displayGeneration !== this.displayGeneration) return false
        const entries = Array.isArray(data.entries) ? data.entries : []
        this.pickerEntries = append ? [...this.pickerEntries, ...entries] : entries
        this.pickerHasMore = data.hasMore === true
        this.pickerNextOffset = data.nextOffset ?? null
        this.pickerSelected = null
        return true
      } catch (error) {
        if (!controller.signal.aborted && generation === this.pickerGeneration && browserGeneration === this.generation
          && viewGeneration === this.viewGeneration && displayGeneration === this.displayGeneration) this.pickerError = error
        return false
      } finally {
        if (generation === this.pickerGeneration) this.pickerLoading = false
        if (this.pickerController === controller) this.pickerController = null
      }
    },
    async chooseSource(source) {
      if (!this.manual || !this.pickerContext || !['session', 'filesystem'].includes(source?.scope)
        || typeof source.path !== 'string' || !source.path
        || (source.scope === 'filesystem' && !source.path.startsWith('/'))) return null
      const generation = this.generation
      const viewGeneration = this.viewGeneration
      const displayGeneration = this.displayGeneration
      this.transferring = true
      this.updateViewOnly()
      try {
        const data = await this.request((config) => api.post(`/browsers/${this.integrationId}/upload-source`, {
          source: { scope: source.scope, path: source.path }
        }, config))
        if (!data || generation !== this.generation || viewGeneration !== this.viewGeneration || displayGeneration !== this.displayGeneration) return null
        this.uploadId = data.chosen ? null : data.uploadId
        return data
      } finally {
        if (generation === this.generation && viewGeneration === this.viewGeneration && displayGeneration === this.displayGeneration) {
          this.transferring = false
          this.updateViewOnly()
        }
      }
    },
    async uploadPickerFile(file) {
      if (!file || !this.manual || !this.pickerContext
        || (this.pickerScope === 'filesystem' && !this.pickerContext.filesystemImport)) return null
      const scope = this.pickerScope
      const directory = this.pickerDirectories[scope]
      const generation = this.generation
      const viewGeneration = this.viewGeneration
      const displayGeneration = this.displayGeneration
      const pickerGeneration = this.pickerGeneration
      this.transferring = true
      this.updateViewOnly()
      try {
        const form = new FormData()
        form.append('file', file, file.name)
        const uploaded = await this.request((config) => api.post('/workspace/files', form, {
          signal: config.signal,
          suppressErrorToast: true,
          params: this.pickerParams({ parent: directory }, false)
        }))
        if (!uploaded?.entry || generation !== this.generation || viewGeneration !== this.viewGeneration
          || displayGeneration !== this.displayGeneration || pickerGeneration !== this.pickerGeneration) return null
        const data = await this.request((config) => api.post(`/browsers/${this.integrationId}/upload-source`, {
          source: { scope, path: uploaded.entry.path }
        }, config))
        if (!data || generation !== this.generation || viewGeneration !== this.viewGeneration
          || displayGeneration !== this.displayGeneration || pickerGeneration !== this.pickerGeneration) return null
        this.pickerSelected = uploaded.entry
        this.pickerEntries = [...this.pickerEntries.filter((entry) => entry.path !== uploaded.entry.path), uploaded.entry]
        this.uploadId = data.chosen ? null : data.uploadId
        return data
      } finally {
        if (generation === this.generation && viewGeneration === this.viewGeneration && displayGeneration === this.displayGeneration) {
          this.transferring = false
          this.updateViewOnly()
        }
      }
    },
    async download(file) {
      if (!this.manual) return
      const generation = this.generation
      const viewGeneration = this.viewGeneration
      const displayGeneration = this.displayGeneration
      this.transferring = true
      this.updateViewOnly()
      try {
        const data = await this.request((config) => api.get(`/browsers/${this.integrationId}/download/${encodeURIComponent(file.id)}`, {
          ...config, responseType: 'blob'
        }))
        if (!data || generation !== this.generation || viewGeneration !== this.viewGeneration || displayGeneration !== this.displayGeneration) return
        const url = URL.createObjectURL(data)
        const link = document.createElement('a')
        link.href = url
        link.download = file.filename
        link.click()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
      } finally {
        if (generation === this.generation && viewGeneration === this.viewGeneration && displayGeneration === this.displayGeneration) {
          this.transferring = false
          this.updateViewOnly()
        }
      }
    },
    syncContext({ sessionId, agentId, available, tabPresent }) {
      if (this.sessionId && (!available || (sessionId === this.sessionId && (this.agentId !== agentId || !tabPresent)))) this.release()
    },
    release({ close = false, replaced = false, preserveRequest = false, preserveCandidate = false } = {}) {
      if (close && !this.ownsDisplay) return
      if (!preserveCandidate) this.cancelCandidate()
      if (!preserveRequest) this.requestRefresh?.controller.abort()
      const retained = { displayedSessionId: this.displayedSessionId, displayGeneration: this.displayGeneration + 1,
        requestAttemptId: this.requestAttemptId,
        ...(preserveCandidate ? { candidate: this.candidate } : {}),
        ...(preserveRequest ? { authorizedRequest: this.authorizedRequest, requestOpening: this.requestOpening, requestRefresh: this.requestRefresh } : {}) }
      const socket = this.socket
      const generation = this.generation + 1
      const { activationKey, activationAttempted } = this
      const context = { integrationId: this.integrationId, sessionId: this.sessionId, agentId: this.agentId }
      const stoppedContext = close || replaced ? JSON.stringify([this.integrationId, this.sessionId, this.agentId]) : this.stoppedContext
      // Replaced views must never send input or runtime-control messages.
      if (replaced) {
        this.controlKeys = []
        if (this.rfb) this.rfb.viewOnly = true
      }
      this.generation = generation
      this.clearView()
      const viewGeneration = this.viewGeneration
      clearTimeout(this.resizeTimer)
      this.$reset()
      Object.assign(this, retained, { activationKey, activationAttempted: close || replaced || activationAttempted, stoppedContext })
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
