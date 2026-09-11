// @vitest-environment jsdom
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useBrowserStore } from '../src/stores/browser'
import { useControlCenterStore } from '../src/stores/control-center'
import { useBrowserSetupStore } from '../src/stores/browser-setup'
import { useIntegrationStore } from '../src/stores/integrations'
import { browserControlCenterAvailable } from '../src/control-center/manifest'
import { browserNavigationUrl, browserSocketUrl, clipboardFits, setupInputChunks, browserStatusLabel } from '../src/utils/browser'
import { apiErrorMessage } from '../src/utils/api-errors'
import { FakeWebSocket } from './terminal-websocket'

const { api, notifyError, viewers, dark } = vi.hoisted(() => ({
  api: { get: vi.fn(), put: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  notifyError: vi.fn(), viewers: [], dark: { active: false }
}))
vi.mock('quasar', async (importOriginal) => ({
  ...await importOriginal(),
  Dark: { get isActive() { return dark.active } }
}))
vi.mock('src/boot/axios', () => ({ api }))
vi.mock('src/utils/notifications', () => ({ notifyError }))
vi.mock('@novnc/novnc/lib/rfb.js', () => ({ default: class extends EventTarget {
  constructor(host, url) {
    super()
    this.host = host
    this.url = url
    this.sendKey = vi.fn()
    this.clipboardPasteFrom = vi.fn()
    this.disconnect = vi.fn()
    host.appendChild(document.createElement('canvas'))
    viewers.push(this)
  }
  get scaleViewport() { return this._scaleViewport }
  set scaleViewport(value) { this._scaleViewport = value; this.scaleViewportSets = (this.scaleViewportSets || 0) + 1 }
} }))

const context = { sessionId: 'session-1', agentId: 'agent-1', integrationId: 'browser-1' }
const instance = { id: 'browser-1', name: 'Browser', executionAgentId: 'agent-1', adapterType: 'browser', enabled: true, config: {} }
const state = { type: 'state', browserId: 'runtime-1', viewId: '00000000-0000-4000-8000-000000000001', attached: false, busy: false, canAttach: true, attachReason: null }
const transferConfig = () => ({ params: { browserId: 'runtime-1', sessionId: 'session-1', viewId: state.viewId }, signal: expect.any(AbortSignal), suppressErrorToast: true })
const navigation = { type: 'navigation', browserId: 'runtime-1',
  tabs: [{ id: 'tab-1', title: 'Example', url: 'https://example.com/', loading: false }],
  activeTabId: 'tab-1', canGoBack: false, canGoForward: false, loading: false, nativeChrome: false }
let browser, socket
beforeEach(() => {
  vi.clearAllMocks()
  dark.active = false
  setActivePinia(createPinia())
  FakeWebSocket.instances = []
  viewers.length = 0
  vi.stubGlobal('WebSocket', FakeWebSocket)
  browser = useBrowserStore()
  browser.setDisplayedSession(context.sessionId)
})
afterEach(() => {
  dark.active = false
  browser.release()
  useBrowserSetupStore().release()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it.each([[false, 'light'], [true, 'dark']])('opens new browser runtimes with the MatoLoop color scheme (%s)', (enabled, expected) => {
  dark.active = enabled
  browser.connect(context)
  expect(new URL(browser.socket.url).searchParams.get('colorScheme')).toBe(expected)
})
async function open() {
  browser.release()
  browser.connect(context)
  socket = FakeWebSocket.instances.at(-1)
  socket.open()
  socket.message(state)
  await browser.mountView(document.createElement('div'))
  viewers.at(-1).dispatchEvent(new Event('connect'))
  expect(socket.send).toHaveBeenCalledExactlyOnceWith('{"type":"navigation","action":"state"}')
  socket.message({ ...navigation, completed: true })
  socket.send.mockClear()
  return viewers.at(-1)
}

it.each([false, true])('subscribes only on live noVNC connect, including attached=%s, without polling or attaching', async (attached) => {
  browser.connect(context)
  const control = browser.socket
  control.open()
  control.message({ ...state, attached })
  await browser.mountView(document.createElement('div'))
  const rfb = viewers.at(-1)
  expect(control.send).not.toHaveBeenCalled()
  rfb.dispatchEvent(new Event('connect'))
  expect(control.send).toHaveBeenCalledExactlyOnceWith('{"type":"navigation","action":"state"}')
  expect(browser.navigationPending).toBe(true)
  expect(rfb.viewOnly).toBe(true)
  rfb.dispatchEvent(new Event('connect'))
  control.message({ ...navigation, completed: false })
  expect(browser.navigationPending).toBe(true)
  control.message({ ...navigation, completed: true })
  expect(browser.navigationPending).toBe(false)
  expect(browser.attached).toBe(attached)
  expect(rfb.viewOnly).toBe(attached)
  vi.useFakeTimers()
  await browser.mountView(document.createElement('div'))
  await vi.advanceTimersByTimeAsync(10000)
  expect(control.send).toHaveBeenCalledTimes(1)
  expect(FakeWebSocket.instances).toHaveLength(1)
})

it('balances the fixed DevTools chord, releases on failure, and rejects view-only or locked input', async () => {
  const rfb = await open()
  const chord = [
    [0xffe3, 'ControlLeft', true], [0xffe1, 'ShiftLeft', true], [0x49, 'KeyI', true],
    [0x49, 'KeyI', false], [0xffe1, 'ShiftLeft', false], [0xffe3, 'ControlLeft', false]
  ]
  browser.controlKey('ControlRight', true)
  browser.devToolsChord()
  expect(rfb.sendKey.mock.calls).toEqual([[0xffe4, 'ControlRight', true], [0xffe4, 'ControlRight', false], ...chord])
  expect(browser.controlKeys).toEqual([])
  rfb.sendKey.mockClear()
  rfb.sendKey.mockImplementation((keysym, code, down) => { if (code === 'KeyI' && down) throw new Error('Send failed') })
  expect(() => browser.devToolsChord()).toThrow('Send failed')
  expect(rfb.sendKey.mock.calls).toEqual(chord)
  rfb.sendKey.mockReset()
  for (const field of ['attached', 'busy', 'transferring', 'submitting', 'navigationPending']) {
    browser[field] = true
    browser.devToolsChord()
    browser[field] = false
  }
  rfb.viewOnly = true
  browser.devToolsChord()
  expect(rfb.sendKey).not.toHaveBeenCalled()
  expect(socket.send).not.toHaveBeenCalled()
})

it('uses only same-origin control and binary view URLs and explicitly confirmed attachment', async () => {
  const rfb = await open()
  expect(socket.url).toBe('ws://localhost:3000/api/browsers/browser-1?sessionId=session-1&colorScheme=light')
  expect(rfb.url).toContain(`/api/browsers/browser-1/view?sessionId=session-1&browserId=runtime-1&viewId=${state.viewId}`)
  expect(socket.send).not.toHaveBeenCalled()
  expect(browser.manual).toBe(true)
  expect(rfb.viewOnly).toBe(false)
  browser.setAttachment(true)
  expect(socket.send).toHaveBeenLastCalledWith('{"type":"attach"}')
  expect(browser.attached).toBe(false)
  expect(rfb.viewOnly).toBe(true)
  socket.message({ ...state, attached: true })
  expect(browser.attached).toBe(true)
  expect(browser.submitting).toBeNull()
  browser.setAttachment(false)
  expect(browser.attached).toBe(true)
  expect(socket.send).toHaveBeenLastCalledWith('{"type":"detach"}')
  socket.message(state)
  expect(browser.manual).toBe(true)
  expect(rfb.viewOnly).toBe(false)
})

it('keeps occupied offers separate from control, refreshes stale offers, and takes over only the offered binding', async () => {
  browser.connect(context)
  const control = browser.socket
  control.open()
  control.message({ type: 'availability', status: 'waiting', canTakeover: false })
  browser.takeover()
  expect(control.send).not.toHaveBeenCalled()
  const offer = { type: 'availability', status: 'occupied', browserId: 'runtime-1', viewId: 'other-view',
    ownerSession: { id: 'session-2', title: 'Other session' }, busy: true, canTakeover: true }
  control.message(offer)
  expect(browser.offer).toEqual(offer)
  expect(browser.browserId).toBeNull()
  expect(browser.viewId).toBeNull()
  await browser.mountView(document.createElement('div'))
  expect(viewers).toHaveLength(0)
  expect(await browser.transferClipboard()).toBeNull()
  expect(await browser.transfer('downloads')).toBeNull()
  browser.setAttachment(true)
  browser.takeover()
  browser.takeover()
  expect(control.send).toHaveBeenCalledExactlyOnceWith('{"type":"takeover","browserId":"runtime-1","viewId":"other-view"}')
  expect(browser.status).toBe('takingOver')
  control.message({ ...offer, viewId: 'newer-offer' })
  expect(browser.status).toBe('occupied')
  expect(control.send).toHaveBeenCalledTimes(1)
  browser.takeover()
  expect(control.send).toHaveBeenLastCalledWith('{"type":"takeover","browserId":"runtime-1","viewId":"newer-offer"}')
  control.message({ type: 'availability', status: 'takingOver', browserId: 'runtime-1' })
  expect(browser.viewId).toBeNull()
  control.message(state)
  expect(browser.offer).toBeNull()
  expect(browser.viewId).toBe(state.viewId)
  await browser.mountView(document.createElement('div'))
  expect(viewers[0].url).toContain(`viewId=${state.viewId}`)
  expect(viewers[0].url).not.toContain('newer-offer')
  expect(control.send).not.toHaveBeenCalledWith('{"type":"attach"}')
  expect(notifyError).not.toHaveBeenCalled()
})

it.each(['message', 'close'])('leaves replaced views quiet without automatic reclaim or old shutdown messages (%s)', async (event) => {
  const rfb = await open()
  browser.controlKey('ControlLeft', true)
  rfb.sendKey.mockClear()
  rfb.disconnect.mockImplementation(() => rfb.dispatchEvent(new Event('disconnect')))
  if (event === 'message') socket.message({ type: 'replaced' })
  else socket.onclose({ code: 4001 })
  expect(browser.status).toBe('replaced')
  expect(browser.ready).toBe(false)
  expect(browser.viewId).toBeNull()
  expect(rfb.host.parentElement).toBeNull()
  expect(rfb.sendKey).not.toHaveBeenCalled()
  socket.onclose({ code: 4001 })
  socket.onerror()
  socket.message(state)
  for (const event of ['disconnect', 'securityfailure', 'credentialsrequired']) rfb.dispatchEvent(new Event(event))
  browser.activate(null, null, false)
  browser.activate('selected', context, true)
  await browser.mountView(document.createElement('div'))
  expect(FakeWebSocket.instances).toHaveLength(1)
  expect(socket.send).not.toHaveBeenCalled()
  expect(notifyError).not.toHaveBeenCalled()
  browser.connect(context)
  expect(FakeWebSocket.instances).toHaveLength(2)
  expect(browser.status).toBe('connecting')
})

it('only explicitly closes the runtime, then releases the viewer and consumes automatic activation', async () => {
  await open()
  browser.release({ close: true })
  expect(socket.send.mock.calls).toEqual([['{"type":"close"}'], ['{"type":"release"}']])
  browser.activate(null, null, false)
  browser.activate('selected', context, true)
  expect(FakeWebSocket.instances).toHaveLength(1)
  browser.connect(context)
  expect(FakeWebSocket.instances).toHaveLength(2)
})

it('accepts a changed controlling view on the same runtime and preserves it through subsequent stream resets', async () => {
  const old = await open()
  const host = old.host.parentElement
  let fail
  api.get.mockImplementationOnce(() => new Promise((resolve, reject) => { fail = reject }))
  const pending = browser.transferClipboard()
  const signal = api.get.mock.calls.at(-1)[1].signal
  const nextState = { ...state, viewId: '00000000-0000-4000-8000-000000000002' }
  socket.message(nextState)
  expect(signal.aborted).toBe(true)
  fail(new Error('Old view request failed'))
  expect(await pending).toBeNull()
  expect(browser.status).toBe('open')
  expect(browser.viewId).toBe(nextState.viewId)
  expect(browser.navigation).toBeNull()
  await browser.mountView(host)
  const next = viewers.at(-1)
  expect(next.url).toContain(`viewId=${nextState.viewId}`)
  old.dispatchEvent(new Event('disconnect'))
  next.dispatchEvent(new Event('connect'))
  socket.message({ ...navigation, completed: true })
  expect(browser.manual).toBe(true)
  socket.message({ type: 'view.reset' })
  socket.message(nextState)
  await vi.waitFor(() => expect(viewers).toHaveLength(3))
  expect(viewers[2].url).toBe(next.url)
  expect(socket.send).not.toHaveBeenCalledWith('{"type":"attach"}')
  expect(socket.close).not.toHaveBeenCalled()
  expect(notifyError).not.toHaveBeenCalled()
})

it.each(['read', 'write', 'copy', 'upload', 'choose', 'downloads', 'download'])('aborts and drops old %s responses/errors across A -> B -> A on the shared runtime', async (action) => {
  for (const reject of [false, true]) {
    await open()
    browser.uploadId = 'old-upload'
    let finish, config
    const method = ['read', 'downloads', 'download'].includes(action) ? 'get' : action === 'write' ? 'put' : 'post'
    api[method].mockImplementationOnce((...args) => {
      config = args.at(-1)
      return new Promise((resolve, fail) => { finish = reject ? fail : resolve })
    })
    const pending = action === 'read' ? browser.transferClipboard()
      : action === 'write' ? browser.transferClipboard('text')
        : action === 'copy' ? browser.copyClipboard()
          : action === 'download' ? browser.download({ id: 'old-file', filename: 'old.txt' })
            : browser.transfer(action, new File(['text'], 'file.txt'))
    expect(config).toEqual({ ...transferConfig(), ...(action === 'download' ? { responseType: 'blob' } : {}) })
    socket.message({ type: 'replaced' })
    expect(config.signal.aborted).toBe(true)
    browser.setDisplayedSession('session-2')
    browser.connect({ ...context, sessionId: 'session-2' })
    browser.socket.open()
    browser.socket.message({ ...state, viewId: 'view-b' })
    browser.release()
    browser.setDisplayedSession(context.sessionId)
    browser.connect(context)
    browser.socket.open()
    browser.socket.message({ ...state, viewId: 'view-a-new' })
    browser.uploadId = 'new-upload'
    browser.transferring = true
    finish(reject ? new Error('Old request failed') : { data: { text: 'old', uploadId: 'old', ok: true, downloads: ['old'] } })
    await pending
    expect(browser.viewId).toBe('view-a-new')
    expect(browser.uploadId).toBe('new-upload')
    expect(browser.downloads).toEqual([])
    expect(browser.transferring).toBe(true)
    expect(notifyError).not.toHaveBeenCalled()
  }
})

it('keeps real navigation state separate from explicit completion, other actions, and stale sockets', async () => {
  const rfb = await open()
  browser.navigation = null
  expect(browser.navigate('new')).toBe(false)
  socket.message(navigation)
  expect(browser.canNavigate).toBe(true)
  browser.navigate('navigate', { url: 'https://new.example/' })
  expect(socket.send).toHaveBeenLastCalledWith(JSON.stringify({ type: 'navigation', action: 'navigate', tabId: 'tab-1', url: 'https://new.example/' }))
  expect(browser.activeTab.url).toBe('https://example.com/')
  expect(rfb.viewOnly).toBe(true)
  expect(browser.navigate('new')).toBe(false)
  browser.setAttachment(true)
  expect(await browser.transferClipboard('text')).toBeNull()
  expect(socket.send).toHaveBeenCalledTimes(1)
  socket.message({ ...navigation, loading: true, completed: false })
  socket.message(state)
  expect(browser.navigationPending).toBe(true)
  socket.message({ ...navigation, completed: true })
  expect(browser.navigationPending).toBe(false)
  expect(rfb.viewOnly).toBe(false)
  browser.setAttachment(true)
  socket.message(navigation)
  socket.message({ type: 'error', operation: 'navigation', error: { code: 'browser.actionFailed' } })
  expect(browser.submitting).toBe('attach')
  socket.message({ ...state, attached: true })
  expect(browser.navigate('chrome', { nativeChrome: true })).toBe(false)
  expect(browser.navigate('state')).toBe(true)
  socket.message({ ...navigation, completed: true })
  expect(browser.attached).toBe(true)
  expect(browser.manual).toBe(false)
  const oldSocket = socket
  await open()
  oldSocket.message({ ...navigation, nativeChrome: true, completed: true })
  socket.message({ ...navigation, nativeChrome: true, browserId: 'old-runtime' })
  expect(browser.navigation.nativeChrome).toBe(false)
  expect(browser.navigationPending).toBe(false)
})

it('releases navigation errors without closing a healthy runtime, polls nothing, and binds every tab action', async () => {
  const rfb = await open()
  socket.message(navigation)
  vi.useFakeTimers()
  await vi.advanceTimersByTimeAsync(10000)
  expect(socket.send).not.toHaveBeenCalled()
  for (const action of ['select', 'close', 'navigate', 'back', 'forward', 'reload', 'stop']) {
    expect(browser.navigate(action, { tabId: 'removed-tab' })).toBe(false)
    expect(browser.navigate(action, { tabId: 'tab-1' })).toBe(true)
    expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toMatchObject({ type: 'navigation', action, tabId: 'tab-1' })
    socket.message({ type: 'error', operation: 'resize', error: { code: 'browser.actionFailed' } })
    expect(browser.navigationPending).toBe(true)
    socket.message({ type: 'error', operation: 'navigation', error: { code: 'browser.invalidTarget', message: 'unsafe detail' } })
    expect(browser.navigationPending).toBe(false)
    expect(browser.activeTab.url).toBe(navigation.tabs[0].url)
    expect(browser.manual).toBe(true)
    expect(rfb.viewOnly).toBe(false)
  }
  expect(socket.close).not.toHaveBeenCalled()
  expect(rfb.disconnect).not.toHaveBeenCalled()
  socket.message({ ...navigation, tabs: Array.from({ length: 32 }, (_, id) => ({ ...navigation.tabs[0], id: `tab-${id}` })) })
  expect(browser.navigate('new')).toBe(false)
})

it.each([
  ['example.com/path?q=1', 'https://example.com/path?q=1'],
  ['example.com:8443', 'https://example.com:8443/'],
  ['localhost:3000/path', 'http://localhost:3000/path'],
  ['127.0.0.1:8080', 'http://127.0.0.1:8080/'],
  ['127.1:8080', 'http://127.0.0.1:8080/'],
  ['[::1]:8080', 'http://[::1]:8080/'],
  ['https://localhost:3000', 'https://localhost:3000/'],
  ['127.evil.example', 'https://127.evil.example/'],
  [' about:blank ', 'about:blank'],
  ['', null], ['two search words', null], ['https:example.com', null],
  ['javascript:alert(1)', null], ['data:text/html,test', null], ['file:///tmp/test', null],
  ['chrome://settings', null], ['about:blank#other', null], ['https://user:pass@example.com', null],
  ['https://example.com:99999', null], ['https://[::1', null], ['https://exa\\mple.com', null],
  [`https://example.com/${'a'.repeat(4096)}`, null]
])('normalizes manual address %s without search or unsafe schemes', (input, expected) => {
  expect(browserNavigationUrl(input)).toBe(expected)
})

it('makes only one connection attempt per activation and reuses matching connecting/open sockets', async () => {
  browser.activate(null, context, true)
  browser.activate('selected', context, false)
  expect(FakeWebSocket.instances).toHaveLength(0)
  browser.activate('selected', context, true)
  const control = browser.socket
  browser.connect(context)
  expect(browser.socket).toBe(control)
  control.open()
  control.message(state)
  await browser.mountView(document.createElement('div'))
  const rfb = viewers[0]
  rfb.dispatchEvent(new Event('connect'))
  browser.activate(null, context, false)
  browser.activate('selected', context, true)
  expect(browser.rfb).toBe(rfb)
  expect(browser.socket).toBe(control)
  control.onclose()
  for (const eligible of [true, false, true]) browser.activate('selected', context, eligible)
  expect(FakeWebSocket.instances).toHaveLength(1)
  browser.activate(null, context, false)
  browser.activate('selected', context, true)
  expect(FakeWebSocket.instances).toHaveLength(2)
  expect(control.send).not.toHaveBeenCalledWith('{"type":"attach"}')
})

it('coalesces actual resolution requests serially, consumes resize errors and never replaces the view', async () => {
  const rfb = await open()
  vi.useFakeTimers()
  const box = vi.spyOn(browser.viewParent, 'getClientRects').mockReturnValue([{ width: 1400, height: 900 }])
  browser.viewVisible = true
  browser.resize(1000.9, 700.9)
  browser.resize(1100.5, 710.5)
  await vi.advanceTimersByTimeAsync(199)
  expect(socket.send).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1)
  expect(socket.send).toHaveBeenLastCalledWith('{"type":"resize","width":1100,"height":710}')
  browser.resize(1200, 720)
  browser.resize(1300, 730)
  await vi.advanceTimersByTimeAsync(1000)
  expect(socket.send).toHaveBeenCalledTimes(1)
  socket.message({ type: 'resized', browserId: 'old-runtime', width: 1100, height: 710 })
  expect(browser.resizeInFlight).not.toBeNull()
  socket.message({ type: 'resized', browserId: 'runtime-1', width: 1100, height: 710 })
  expect(browser.desktopSize).toEqual({ width: 1100, height: 710 })
  await vi.advanceTimersByTimeAsync(200)
  expect(socket.send).toHaveBeenLastCalledWith('{"type":"resize","width":1300,"height":730}')
  browser.setAttachment(true)
  socket.message({ type: 'error', operation: 'resize', error: { code: 'browser.actionFailed' } })
  expect(browser.submitting).toBe('attach')
  expect(browser.resizeInFlight).toBeNull()
  await vi.advanceTimersByTimeAsync(2000)
  expect(socket.send).toHaveBeenCalledTimes(3)
  expect(socket.close).not.toHaveBeenCalled()
  expect(browser.rfb).toBe(rfb)
  expect(rfb.resizeSession).toBe(false)
  browser.resize(0, 0)
  browser.resize(NaN, 100)
  browser.viewVisible = false
  browser.resize(100, 100)
  await vi.advanceTimersByTimeAsync(200)
  expect(socket.send).toHaveBeenCalledTimes(3)
  browser.viewVisible = true
  browser.resize(100, 5000)
  box.mockReturnValue([])
  await vi.advanceTimersByTimeAsync(200)
  expect(socket.send).toHaveBeenCalledTimes(3)
  box.mockReturnValue([{ width: 100, height: 5000 }])
  browser.resize(100, 5000)
  await vi.advanceTimersByTimeAsync(200)
  expect(socket.send).toHaveBeenCalledTimes(3)
  socket.message({ ...state, attached: true })
  await vi.advanceTimersByTimeAsync(200)
  expect(socket.send).toHaveBeenLastCalledWith('{"type":"resize","width":320,"height":2160}')
  browser.release()
  await vi.advanceTimersByTimeAsync(2000)
  expect(FakeWebSocket.instances).toHaveLength(1)
})

it('mounts a pending noVNC import in the latest parent without starting a second stream', async () => {
  browser.connect(context)
  browser.socket.open()
  browser.socket.message(state)
  const old = document.createElement('div')
  const current = document.createElement('div')
  await Promise.all([browser.mountView(old), browser.mountView(current)])
  expect(viewers).toHaveLength(1)
  expect(old.childElementCount).toBe(0)
  expect(viewers[0].host.parentElement).toBe(current)
})

it('balances only clipboard chords and releases held Ctrl before locking manual input', async () => {
  const rfb = await open()
  browser.controlKey('ControlRight', true)
  browser.clipboardChord('v')
  expect(rfb.sendKey.mock.calls).toEqual([
    [0xffe4, 'ControlRight', true], [0xffe4, 'ControlRight', false],
    [0xffe3, 'ControlLeft', true], [118, 'KeyV', true],
    [118, 'KeyV', false], [0xffe3, 'ControlLeft', false]
  ])
  browser.controlKey('ControlLeft', true)
  socket.message({ ...state, attached: true })
  expect(rfb.sendKey).toHaveBeenLastCalledWith(0xffe3, 'ControlLeft', false)
  expect(browser.controlKeys).toEqual([])
  rfb.sendKey.mockClear()
  browser.clipboardChord('c')
  browser.controlKey('ControlLeft', true)
  expect(rfb.sendKey).not.toHaveBeenCalled()
})

it('allows takeover while busy but locks input until the action settles and respects profile access', async () => {
  const rfb = await open()
  socket.message({ ...state, busy: true, attached: true })
  browser.setAttachment(false)
  expect(socket.send).toHaveBeenCalledExactlyOnceWith('{"type":"detach"}')
  expect(browser.attached).toBe(true)
  expect(browser.submitting).toBe('detach')
  expect(await browser.transferClipboard('unsafe')).toBeNull()
  expect(rfb.viewOnly).toBe(true)
  socket.message({ ...state, busy: true, attached: false })
  expect(browser.submitting).toBeNull()
  expect(browser.manual).toBe(false)
  expect(rfb.viewOnly).toBe(true)
  browser.setAttachment(true)
  expect(socket.send).toHaveBeenCalledTimes(1)
  socket.message(state)
  expect(browser.manual).toBe(true)
  expect(rfb.viewOnly).toBe(false)
  socket.send.mockClear()
  socket.message({ ...state, canAttach: false, attachReason: 'profileExcluded' })
  browser.setAttachment(true)
  expect(socket.send).not.toHaveBeenCalled()
  expect(browser.manual).toBe(true)
  socket.message(state)
  browser.setAttachment(true)
  socket.message({ type: 'error', error: { code: 'browser.busy', message: 'unsafe server text' } })
  expect(browser.attached).toBe(false)
  expect(browser.submitting).toBeNull()
  expect(rfb.viewOnly).toBe(false)
  expect(notifyError).toHaveBeenCalledWith({ error: { code: 'browser.busy', message: 'unsafe server text' } })
})

it('preserves control and noVNC across collapse/remount but rejects events from old generations', async () => {
  const old = await open()
  const oldSocket = socket
  const host = document.createElement('div')
  await browser.mountView(host)
  expect(host.firstChild).toBe(old.host)
  expect(viewers).toHaveLength(1)
  browser.syncContext({ ...context, available: true, tabPresent: true, onChatRoute: true })
  expect(oldSocket.close).not.toHaveBeenCalled()
  browser.release()
  const current = await open()
  oldSocket.message({ ...state, attached: true })
  oldSocket.onclose()
  old.dispatchEvent(new Event('disconnect'))
  old.dispatchEvent(new CustomEvent('clipboard', { detail: { text: 'old secret' } }))
  expect(browser.ready).toBe(true)
  expect(browser.attached).toBe(false)
  expect(api.get).not.toHaveBeenCalled()
  expect(browser.rfb).toBe(current)
  expect(current.disconnect).not.toHaveBeenCalled()
  expect(socket.send).not.toHaveBeenCalled()
})

it('invalidates a pending noVNC import when the context closes', async () => {
  browser.connect(context)
  const control = FakeWebSocket.instances.at(-1)
  control.open()
  control.message(state)
  const mounting = browser.mountView(document.createElement('div'))
  browser.release()
  await mounting
  expect(viewers).toHaveLength(0)
  expect(browser.rfb).toBeNull()
})

it.each([
  { agentId: 'other' }, { available: false }, { tabPresent: false }
])('disconnects on context invalidation %j without automatic reconnect', async (change) => {
  const rfb = await open()
  browser.syncContext({ ...context, available: true, tabPresent: true, onChatRoute: true, ...change })
  expect(socket.close).toHaveBeenCalledOnce()
  expect(rfb.disconnect).toHaveBeenCalledOnce()
  expect(browser.status).toBe('closed')
  expect(socket.send).toHaveBeenLastCalledWith('{"type":"release"}')
  expect(socket.send).not.toHaveBeenCalledWith('{"type":"close"}')
  browser.syncContext({ ...context, available: true, tabPresent: true, onChatRoute: true })
  expect(FakeWebSocket.instances).toHaveLength(1)
})

it.each([false, true])('retains the owner, live viewer and model attachment across A -> load gap -> B -> A for over 30 seconds (attached=%s)', async (attached) => {
  const rfb = await open()
  socket.message({ ...state, attached })
  const original = socket
  browser.setDisplayedSession(null)
  browser.syncContext({ sessionId: null, agentId: null, available: true, tabPresent: true })
  browser.setDisplayedSession('session-2')
  browser.syncContext({ sessionId: 'session-2', agentId: 'other-agent', available: true, tabPresent: false })
  browser.activate('B', { ...context, sessionId: 'session-2' }, true)
  expect(browser.displayStatus).toBe('occupied')
  expect(browser.displayNavigation).toBeNull()
  expect(browser.activeTab).toBeNull()
  expect(browser.ready).toBe(false)
  expect(browser.manual).toBe(false)
  expect(rfb.viewOnly).toBe(true)
  expect(rfb.host.parentElement).toBeNull()
  await browser.mountView(document.createElement('div'))
  for (const action of ['state', 'new', 'navigate', 'close', 'chrome']) expect(browser.navigate(action)).toBe(false)
  browser.setAttachment(false)
  browser.setAttachment(true)
  browser.release({ close: true })
  browser.takeover()
  browser.controlKey('ControlLeft', true)
  browser.clipboardChord('v')
  browser.devToolsChord()
  browser.toggleViewportScale()
  browser.resize(1000, 700)
  expect(await browser.transferClipboard()).toBeNull()
  expect(await browser.transferClipboard('secret')).toBeNull()
  expect(await browser.transfer('downloads')).toBeNull()
  await browser.download({ id: 'file', filename: 'secret' })
  vi.useFakeTimers()
  await vi.advanceTimersByTimeAsync(65000)
  expect(original.send).not.toHaveBeenCalled()
  expect(original.close).not.toHaveBeenCalled()
  expect(rfb.disconnect).not.toHaveBeenCalled()
  expect(api.get).not.toHaveBeenCalled()
  expect(browser.attached).toBe(attached)
  // The original model can still publish live browser observations while parked.
  original.message({ ...navigation, tabs: [{ ...navigation.tabs[0], title: 'Still attached' }] })
  browser.setDisplayedSession(context.sessionId)
  browser.activate('A', context, true)
  const host = document.createElement('div')
  await browser.mountView(host)
  expect(browser.activeTab.title).toBe('Still attached')
  expect(browser.socket).toBe(original)
  expect(browser.rfb).toBe(rfb)
  expect(browser.attached).toBe(attached)
  expect(rfb.viewOnly).toBe(attached)
  expect(rfb.scaleViewportSets).toBe(2)
  expect(host.firstChild).toBe(rfb.host)
  expect(FakeWebSocket.instances).toHaveLength(1)
})

it.each(['clipboard', 'upload', 'downloads', 'download'])('aborts parked %s transfers without letting late A responses unlock a fresh A transfer', async (action) => {
  await open()
  let finish, signal
  const method = action === 'upload' ? 'post' : 'get'
  api[method].mockImplementationOnce((...args) => {
    signal = args.at(-1).signal
    return new Promise((resolve) => { finish = resolve })
  })
  const pending = action === 'clipboard' ? browser.transferClipboard()
    : action === 'download' ? browser.download({ id: 'file', filename: 'old' })
      : browser.transfer(action, new File(['data'], 'file'))
  browser.setDisplayedSession('session-2')
  expect(signal.aborted).toBe(true)
  browser.setDisplayedSession(context.sessionId)
  await browser.mountView(document.createElement('div'))
  browser.transferring = true
  finish({ data: { text: 'old', downloads: ['old'], uploadId: 'old' } })
  await pending
  expect(browser.transferring).toBe(true)
  expect(browser.downloads).toEqual([])
  expect(browser.uploadId).toBeNull()
  expect(notifyError).not.toHaveBeenCalled()
})

it.each(['accepted', 'error', 'close', 'cancel', 'replaced-first'])('keeps one owner until the explicit candidate handoff is accepted (%s)', async (outcome) => {
  const rfb = await open()
  const original = socket
  original.message({ ...state, attached: true })
  browser.setDisplayedSession('session-2')
  browser.connect({ ...context, sessionId: 'session-2' }, { takeover: true })
  const candidate = browser.candidate.socket
  candidate.open()
  candidate.message({ type: 'availability', status: 'occupied', browserId: state.browserId, viewId: state.viewId, canTakeover: true })
  expect(candidate.send).toHaveBeenCalledExactlyOnceWith(JSON.stringify({ type: 'takeover', browserId: state.browserId, viewId: state.viewId }))
  expect(browser.socket).toBe(original)
  expect(browser.attached).toBe(true)
  expect(original.close).not.toHaveBeenCalled()
  expect(rfb.disconnect).not.toHaveBeenCalled()
  if (outcome === 'error') candidate.message({ type: 'error', error: { code: 'browser.bindingChanged' } })
  if (outcome === 'close') candidate.onclose()
  if (outcome === 'cancel') browser.setDisplayedSession(context.sessionId)
  if (['accepted', 'replaced-first'].includes(outcome)) {
    if (outcome === 'replaced-first') original.message({ type: 'replaced' })
    candidate.message({ ...state, viewId: 'accepted-view' })
    expect(browser.socket).toBe(candidate)
    expect(browser.sessionId).toBe('session-2')
    expect(browser.attached).toBe(false)
    expect(browser.navigation).toBeNull()
    expect(original.send).not.toHaveBeenCalled()
    expect(original.close).toHaveBeenCalledOnce()
    expect(rfb.disconnect).toHaveBeenCalledOnce()
  } else {
    candidate.message({ ...state, viewId: 'late-view' })
    expect(browser.socket).toBe(original)
    expect(browser.rfb).toBe(rfb)
    expect(browser.attached).toBe(true)
    expect(original.close).not.toHaveBeenCalled()
    expect(rfb.disconnect).not.toHaveBeenCalled()
  }
  expect(candidate.send).not.toHaveBeenCalledWith('{"type":"attach"}')
  expect(browser.candidate).toBeNull()
})

it.each(['absent', 'same-owner', 'other-owner'])('claims authorized control once and attaches without a viewer (%s)', async (owner) => {
  let original, rfb
  if (owner !== 'absent') { rfb = await open(); original = socket }
  const sessionId = owner === 'other-owner' ? 'session-2' : context.sessionId
  browser.setDisplayedSession(sessionId)
  const center = useControlCenterStore()
  center.syncSession({ id: sessionId })
  let finish
  const ensureApp = vi.spyOn(center, 'ensureApp').mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  const request = { ...context, sessionId, id: 'request-1', runId: 'run-1' }
  api.get.mockResolvedValue({ data: { request } })
  const pending = browser.reconcileControlRequest(sessionId)
  await vi.waitFor(() => expect(ensureApp).toHaveBeenCalledExactlyOnceWith('browser'))
  browser.activate('request', request, true)
  expect(FakeWebSocket.instances).toHaveLength(owner === 'absent' ? 0 : 1)
  finish()
  await pending
  const control = browser.candidate?.socket || browser.socket
  expect(control.url).toContain(`sessionId=${sessionId}&controlRequestId=request-1`)
  if (original) {
    expect(original.close).not.toHaveBeenCalled()
    expect(rfb.disconnect).not.toHaveBeenCalled()
  }
  control.open()
  control.message({ ...state, viewId: 'requested-view' })
  expect(control.send).toHaveBeenCalledExactlyOnceWith('{"type":"attach.requested","requestId":"request-1"}')
  control.message({ ...state, viewId: 'requested-view', busy: true })
  expect(control.send).toHaveBeenCalledTimes(1)
  control.message({ ...state, viewId: 'requested-view', attached: true })
  await browser.mountView(document.createElement('div'))
  const viewer = viewers.at(-1)
  viewer.dispatchEvent(new Event('connect'))
  expect(viewer.viewOnly).toBe(true)
  expect(control.send.mock.calls.map(([raw]) => JSON.parse(raw).type)).toEqual([
    'attach.requested', 'navigation'
  ])
  for (let i = 0; i < 3; i++) {
    await browser.reconcileControlRequest(sessionId)
    browser.activate('request', request, true)
  }
  expect(browser.attached).toBe(true)
  expect(browser.rfb).toBe(viewer)
  expect(FakeWebSocket.instances).toHaveLength(owner === 'absent' ? 1 : 2)
  expect(control.send.mock.calls.filter(([raw]) => JSON.parse(raw).type.startsWith('attach'))).toEqual([
    [JSON.stringify({ type: 'attach.requested', requestId: 'request-1' })]
  ])
  expect(ensureApp).toHaveBeenCalledOnce()
})

it.each(['same-session-request', 'human-takeover'])('keeps the candidate when the old RFB disconnects before control replacement (%s)', async (mode) => {
  const oldViewer = await open()
  const oldControl = socket
  const request = { ...context, id: 'request-1', runId: 'run-1' }
  if (mode === 'same-session-request') {
    browser.authorizedRequest = request
    browser.activate('request', context, true)
  } else {
    browser.setDisplayedSession('session-2')
    browser.connect({ ...context, sessionId: 'session-2' }, { takeover: true })
  }
  const candidate = browser.candidate.socket
  candidate.open()
  if (mode === 'human-takeover') {
    candidate.message({ type: 'availability', status: 'occupied', browserId: state.browserId, viewId: state.viewId, canTakeover: true })
    expect(candidate.send).toHaveBeenCalledExactlyOnceWith(JSON.stringify({ type: 'takeover', browserId: state.browserId, viewId: state.viewId }))
  }
  oldViewer.dispatchEvent(new Event('disconnect'))
  expect(browser.candidate.socket).toBe(candidate)
  expect(candidate.close).not.toHaveBeenCalled()
  expect(oldControl.close).not.toHaveBeenCalled()
  expect(oldControl.send).not.toHaveBeenCalled()
  if (mode === 'same-session-request') expect(browser.authorizedRequest.id).toBe(request.id)
  oldControl.message({ type: 'replaced' })
  candidate.message({ ...state, viewId: 'new-view' })
  await browser.mountView(document.createElement('div'))
  const viewer = viewers.at(-1)
  viewer.dispatchEvent(new Event('connect'))
  for (const event of ['disconnect', 'connect', 'securityfailure', 'credentialsrequired']) oldViewer.dispatchEvent(new Event(event))
  oldControl.message({ type: 'error', operation: 'control', error: { code: 'browser.bindingChanged' } })
  oldControl.onclose()
  expect(browser.socket).toBe(candidate)
  expect(browser.rfb).toBe(viewer)
  expect(candidate.close).not.toHaveBeenCalled()
  expect(viewer.disconnect).not.toHaveBeenCalled()
  expect(notifyError).not.toHaveBeenCalled()
  if (mode === 'same-session-request') {
    expect(candidate.send).toHaveBeenCalledExactlyOnceWith('{"type":"attach.requested","requestId":"request-1"}')
    candidate.message({ ...state, viewId: 'new-view', attached: true })
    expect(candidate.send.mock.calls.map(([raw]) => JSON.parse(raw).type)).toEqual(['attach.requested', 'navigation'])
  } else {
    expect(candidate.send.mock.calls.map(([raw]) => JSON.parse(raw).type)).toEqual(['takeover', 'navigation'])
    expect(browser.attached).toBe(false)
  }
})

it.each(['browser.bindingChanged', 'browser.notConnected', 'browser.controlTimeout'])('keeps live control after scoped requested attachment failure %s', async (code) => {
  const request = { ...context, id: 'request-1', runId: 'run-1' }
  browser.authorizedRequest = request
  browser.activate('request', context, true)
  const control = browser.socket
  control.open()
  control.message(state)
  await browser.mountView(document.createElement('div'))
  const viewer = viewers.at(-1)
  viewer.dispatchEvent(new Event('connect'))
  expect(control.send).toHaveBeenCalledExactlyOnceWith('{"type":"attach.requested","requestId":"request-1"}')
  control.message({ type: 'error', operation: 'control', error: { code } })
  expect(browser.controlRequestId).toBeNull()
  expect(browser.authorizedRequest).toBeNull()
  expect(browser.submitting).toBeNull()
  expect(browser.socket).toBe(control)
  expect(browser.rfb).toBe(viewer)
  expect(control.close).not.toHaveBeenCalled()
  expect(viewer.disconnect).not.toHaveBeenCalled()
  expect(notifyError).toHaveBeenCalledExactlyOnceWith({ error: { code } })
  expect(control.send.mock.calls.map(([raw]) => JSON.parse(raw).type)).toEqual(['attach.requested', 'navigation'])
  control.message({ ...navigation, completed: true })
  expect(browser.manual).toBe(true)
})

it.each([false, true])('cancels requested attachment without stale attach, teardown or error toast (viewerConnected=%s)', async (connected) => {
  const request = { ...context, id: 'request-1', runId: 'run-1' }
  browser.authorizedRequest = request
  browser.activate('request', context, true)
  const control = browser.socket
  control.open()
  control.message(state)
  await browser.mountView(document.createElement('div'))
  const viewer = viewers.at(-1)
  if (connected) viewer.dispatchEvent(new Event('connect'))
  browser.setAttachment(false)
  viewer.dispatchEvent(new Event('connect'))
  const initial = ['attach.requested', 'detach']
  expect(control.send.mock.calls.map(([raw]) => JSON.parse(raw).type)).toEqual(initial)
  control.message({ type: 'error', operation: 'control', error: { code: 'browser.notConnected' } })
  expect(browser.submitting).toBe('detach')
  expect(control.send.mock.calls.map(([raw]) => JSON.parse(raw).type)).toEqual(initial)
  control.message({ ...state, busy: true })
  expect(control.send.mock.calls.map(([raw]) => JSON.parse(raw).type)).toEqual(initial)
  control.message(state)
  expect(control.send.mock.calls.map(([raw]) => JSON.parse(raw).type)).toEqual([...initial, 'navigation'])
  control.message({ ...navigation, completed: true })
  useControlCenterStore().syncSession({ id: context.sessionId })
  api.get.mockResolvedValueOnce({ data: { request } })
  await browser.reconcileControlRequest(context.sessionId)
  browser.activate('request', context, true)
  await browser.mountView(document.createElement('div'))
  expect(control.send.mock.calls.map(([raw]) => JSON.parse(raw).type)).toEqual([...initial, 'navigation'])
  expect(browser.controlRequestId).toBeNull()
  expect(browser.submitting).toBeNull()
  expect(browser.manual).toBe(true)
  expect(control.close).not.toHaveBeenCalled()
  expect(viewer.disconnect).not.toHaveBeenCalled()
  expect(notifyError).not.toHaveBeenCalled()
  expect(FakeWebSocket.instances).toHaveLength(1)
})

it.each(['denied', 'stale-get', 'stale-open', 'failed-open', 'cancelled-view', 'manual-detach'])('does not leave latent requested attachment or open denied requests (%s)', async (change) => {
  const center = useControlCenterStore()
  center.syncSession({ id: context.sessionId })
  const request = { ...context, id: 'request-1', runId: 'run-1' }
  let finish
  api.get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const ensureApp = vi.spyOn(center, 'ensureApp').mockResolvedValue()
  const pending = browser.reconcileControlRequest(context.sessionId)
  if (change === 'stale-get') browser.setDisplayedSession('session-2')
  if (change === 'failed-open') ensureApp.mockRejectedValueOnce(new Error('Save failed'))
  let finishOpen
  if (change === 'stale-open') ensureApp.mockImplementationOnce(() => new Promise((resolve) => { finishOpen = resolve }))
  finish({ data: { request: change === 'denied' ? null : request } })
  if (change === 'stale-open') {
    await vi.waitFor(() => expect(ensureApp).toHaveBeenCalled())
    browser.setDisplayedSession('session-2')
    finishOpen()
  }
  await pending
  if (['cancelled-view', 'manual-detach'].includes(change)) {
    browser.activate('request', request, true)
    const control = browser.socket
    control.open()
    control.message(state)
    await browser.mountView(document.createElement('div'))
    if (change === 'manual-detach') browser.setAttachment(false)
    else {
      api.get.mockResolvedValueOnce({ data: { request: null } })
      await browser.reconcileControlRequest(context.sessionId)
    }
    const sent = control.send.mock.calls.length
    viewers.at(-1).dispatchEvent(new Event('connect'))
    expect(control.send).toHaveBeenCalledTimes(sent)
    expect(control.send).not.toHaveBeenCalledWith('{"type":"attach"}')
  } else {
    browser.activate('request', request, false)
    expect(FakeWebSocket.instances).toHaveLength(0)
  }
  if (['denied', 'stale-get'].includes(change)) expect(ensureApp).not.toHaveBeenCalled()
})

it('closes the control when the view is lost, and requires an explicit new connection', async () => {
  const rfb = await open()
  socket.message({ ...state, attached: true })
  rfb.dispatchEvent(new Event('disconnect'))
  expect(socket.close).toHaveBeenCalledOnce()
  expect(browser.attached).toBe(false)
  expect(browser.ready).toBe(false)
})

it('switches fit and original size locally without reconnecting or resizing the remote desktop', async () => {
  const rfb = await open()
  const generation = browser.generation
  const viewGeneration = browser.viewGeneration
  expect(rfb.scaleViewport).toBe(true)
  expect(rfb.clipViewport).toBe(false)
  expect(rfb.resizeSession).toBe(false)
  browser.toggleViewportScale()
  expect(browser.scaleViewport).toBe(false)
  expect(rfb.scaleViewport).toBe(false)
  socket.message({ ...state, attached: true, busy: true })
  browser.toggleViewportScale()
  expect(rfb.scaleViewport).toBe(true)
  expect(rfb.viewOnly).toBe(true)
  browser.toggleViewportScale()
  await browser.mountView(document.createElement('div'))
  expect(rfb.scaleViewport).toBe(false)
  expect(rfb.clipViewport).toBe(false)
  expect(rfb.resizeSession).toBe(false)
  expect(browser.generation).toBe(generation)
  expect(browser.viewGeneration).toBe(viewGeneration)
  expect(browser.rfb).toBe(rfb)
  expect(viewers).toHaveLength(1)
  expect(FakeWebSocket.instances).toHaveLength(1)
  expect(socket.send).not.toHaveBeenCalled()
  expect(socket.close).not.toHaveBeenCalled()
  browser.release()
  expect(browser.scaleViewport).toBe(true)
})

it.each([false, true])('reconnects only the announced view after manual unlock (remount=%s)', async (remount) => {
  const old = await open()
  browser.toggleViewportScale()
  const parent = old.host.parentElement
  const generation = browser.generation
  const viewGeneration = browser.viewGeneration
  browser.uploadId = 'staged-file'
  socket.message({ ...state, attached: true, busy: true })
  browser.setAttachment(false)
  socket.message({ ...state, attached: false, busy: true })
  expect(old.viewOnly).toBe(true)
  expect(browser.manual).toBe(false)
  // noVNC may emit disconnect synchronously as well as after the server stream closes.
  old.disconnect.mockImplementationOnce(() => old.dispatchEvent(new Event('disconnect')))
  socket.message({ type: 'view.reset' })
  expect(browser.viewGeneration).toBe(viewGeneration + 1)
  expect(browser.viewStatus).toBe('reset')
  expect(browser.rfb).toBeNull()
  expect(browser.viewHost).toBeNull()
  expect(parent.childElementCount).toBe(0)
  expect(old.disconnect).toHaveBeenCalledOnce()
  expect(browser.generation).toBe(generation)
  expect(browser.socket).toBe(socket)
  expect(browser.browserId).toBe('runtime-1')
  expect(browser.uploadId).toBe('staged-file')
  const nextParent = remount ? document.createElement('div') : parent
  await browser.mountView(nextParent)
  expect(viewers).toHaveLength(1)
  for (const message of [{ ...state, attached: false, busy: true }, { ...state, attached: true }]) {
    socket.message(message)
    await browser.mountView(nextParent)
    expect(browser.viewStatus).toBe('reset')
    expect(viewers).toHaveLength(1)
  }
  socket.message(state)
  await vi.waitFor(() => expect(viewers).toHaveLength(2))
  const current = viewers[1]
  expect(current.scaleViewport).toBe(false)
  expect(current.clipViewport).toBe(false)
  expect(current.resizeSession).toBe(false)
  expect(current.url).toBe(old.url)
  expect(current.host.parentElement).toBe(nextParent)
  expect(browser.manual).toBe(false)
  expect(current.viewOnly).toBe(true)
  for (const event of ['disconnect', 'connect', 'securityfailure', 'credentialsrequired']) old.dispatchEvent(new Event(event))
  expect(browser.rfb).toBe(current)
  expect(browser.viewStatus).toBe('connecting')
  current.dispatchEvent(new Event('connect'))
  socket.message(state)
  expect(browser.manual).toBe(false)
  socket.message({ ...navigation, completed: true })
  expect(browser.manual).toBe(true)
  expect(current.viewOnly).toBe(false)
  expect(browser.attached).toBe(false)
  expect(browser.generation).toBe(generation)
  expect(viewers).toHaveLength(2)
  expect(FakeWebSocket.instances).toHaveLength(1)
  expect(socket.send.mock.calls).toEqual([['{"type":"detach"}'], ['{"type":"navigation","action":"state"}']])
  expect(socket.close).not.toHaveBeenCalled()
  expect(notifyError).not.toHaveBeenCalled()
  current.dispatchEvent(new Event('disconnect'))
  expect(socket.close).toHaveBeenCalledOnce()
  expect(browser.status).toBe('closed')
})

it('invalidates a pending view import on reset and reconnects only after a subsequent unlocked state', async () => {
  browser.connect(context)
  const control = FakeWebSocket.instances.at(-1)
  control.open()
  control.message(state)
  const parent = document.createElement('div')
  const pending = browser.mountView(parent)
  control.message({ type: 'view.reset' })
  await pending
  expect(viewers).toHaveLength(0)
  expect(browser.viewStatus).toBe('reset')
  expect(browser.viewParent).toBe(parent)
  control.message(state)
  await vi.waitFor(() => expect(viewers).toHaveLength(1))
  expect(viewers[0].host.parentElement).toBe(parent)
  expect(control.send).not.toHaveBeenCalled()
  expect(control.close).not.toHaveBeenCalled()
})

it('does not resurrect a reset view after the control connection closes', async () => {
  const old = await open()
  socket.message({ type: 'view.reset' })
  socket.message(state)
  socket.onclose()
  await Promise.resolve()
  await Promise.resolve()
  old.dispatchEvent(new Event('disconnect'))
  socket.message(state)
  expect(browser.status).toBe('closed')
  expect(browser.viewParent).toBeNull()
  expect(browser.rfb).toBeNull()
  expect(viewers).toHaveLength(1)
})

it('uses private UTF-8 clipboard endpoints without noVNC keys, legacy clipboard or hidden local sync', async () => {
  const writeText = vi.fn()
  vi.stubGlobal('navigator', { clipboard: { writeText } })
  const rfb = await open()
  rfb.dispatchEvent(new CustomEvent('clipboard', { detail: { text: 'Untrusted legacy clipboard' } }))
  expect(api.get).not.toHaveBeenCalled()
  expect(writeText).not.toHaveBeenCalled()
  const text = 'Grüße 世界 😀\nzweite\tZeile\r\n'
  const config = transferConfig()
  api.get.mockResolvedValueOnce({ data: { text } })
  expect(await browser.transferClipboard()).toEqual({ text })
  expect(api.get).toHaveBeenCalledExactlyOnceWith('/browsers/browser-1/clipboard', config)
  api.put.mockResolvedValueOnce({ data: { text } })
  expect(await browser.transferClipboard(text)).toEqual({ text })
  expect(api.put).toHaveBeenCalledExactlyOnceWith('/browsers/browser-1/clipboard', { text }, config)
  api.post.mockResolvedValueOnce({ data: { text } })
  expect(await browser.copyClipboard()).toEqual({ text })
  expect(api.post).toHaveBeenCalledExactlyOnceWith('/browsers/browser-1/clipboard/copy', null, config)
  expect(writeText).not.toHaveBeenCalled()
  expect(rfb.sendKey).not.toHaveBeenCalled()
  expect(rfb.clipboardPasteFrom).not.toHaveBeenCalled()
  expect(clipboardFits('a'.repeat(16383))).toBe(true)
  expect(clipboardFits('a'.repeat(16384))).toBe(false)
  expect(clipboardFits('世'.repeat(6000))).toBe(false)
  for (const oversized of ['a'.repeat(16384), '世'.repeat(6000)]) {
    expect(await browser.transferClipboard(oversized)).toBeNull()
    api.get.mockResolvedValueOnce({ data: { text: oversized } })
    expect(await browser.transferClipboard()).toBeNull()
    api.post.mockResolvedValueOnce({ data: { text: oversized } })
    expect(await browser.copyClipboard()).toBeNull()
  }
  expect(api.put).toHaveBeenCalledTimes(1)
  expect(browser.manual).toBe(true)
})

it.each(['get', 'put', 'post'])('guards clipboard generations and does not duplicate Axios errors (%s)', async (method) => {
  await open()
  const transfer = () => method === 'post' ? browser.copyClipboard() : browser.transferClipboard(method === 'put' ? '世界\n\t' : undefined)
  const error = new Error('Axios failure')
  api[method].mockRejectedValueOnce(error)
  await expect(transfer()).rejects.toBe(error)
  expect(notifyError).toHaveBeenCalledExactlyOnceWith(error)
  expect(browser.manual).toBe(true)
  let finish
  api[method].mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const pending = transfer()
  expect(browser.transferring).toBe(true)
  expect(browser.rfb.viewOnly).toBe(true)
  expect(await transfer()).toBeNull()
  await open()
  browser.transferring = true
  finish({ data: { text: 'old clipboard' } })
  expect(await pending).toBeNull()
  expect(browser.transferring).toBe(true)
})

it.each([{ attached: true }, { busy: true }])('blocks clipboard read/write/copy outside manual access (%j)', async (change) => {
  await open()
  socket.message({ ...state, ...change })
  expect(await browser.transferClipboard()).toBeNull()
  expect(await browser.transferClipboard('text')).toBeNull()
  expect(await browser.copyClipboard()).toBeNull()
  expect(api.get).not.toHaveBeenCalled()
  expect(api.put).not.toHaveBeenCalled()
  expect(api.post).not.toHaveBeenCalled()
})

it.each(['reset', 'attach', 'busy'])('discards confirmed copy when manual view changes during POST (%s)', async (change) => {
  await open()
  let finish
  api.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const pending = browser.copyClipboard()
  expect(browser.transferring).toBe(true)
  if (change === 'reset') socket.message({ type: 'view.reset' })
  if (change === 'attach') socket.message({ ...state, attached: true })
  if (change === 'busy') socket.message({ ...state, busy: true })
  finish({ data: { text: 'stale confirmed text' } })
  expect(await pending).toBeNull()
  expect(browser.transferring).toBe(false)
  expect(notifyError).not.toHaveBeenCalled()
})

it('stages opaque uploads and downloads through the exact bound REST API and ignores late results', async () => {
  await open()
  const file = new File(['content'], 'example.txt')
  api.post.mockResolvedValueOnce({ data: { uploadId: 'opaque-id', chosen: false } })
  await browser.transfer('upload', file)
  const [path, form, config] = api.post.mock.calls[0]
  expect(path).toBe('/browsers/browser-1/upload')
  expect(form.get('file')).toBe(file)
  expect(config).toEqual(transferConfig())
  expect(browser.uploadId).toBe('opaque-id')
  const downloads = [{ id: 'download-1', filename: 'report.pdf', size: 12 }]
  api.get.mockResolvedValueOnce({ data: { tabs: [], downloads } })
  await browser.transfer('downloads')
  expect(browser.downloads).toEqual(downloads)
  const createObjectURL = vi.fn().mockReturnValue('blob:download')
  URL.createObjectURL = createObjectURL
  URL.revokeObjectURL = vi.fn()
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  api.get.mockResolvedValueOnce({ data: new Blob(['pdf']) })
  await browser.download(downloads[0])
  expect(api.get).toHaveBeenLastCalledWith('/browsers/browser-1/download/download-1', { ...config, responseType: 'blob' })
  expect(click).toHaveBeenCalledOnce()
  click.mockRestore()
  let resolve
  api.post.mockImplementationOnce(() => new Promise((done) => { resolve = done }))
  const pending = browser.transfer('upload', file)
  expect(browser.transferring).toBe(true)
  expect(browser.rfb.viewOnly).toBe(true)
  browser.release()
  resolve({ data: { uploadId: 'stale', chosen: false } })
  await pending
  expect(browser.uploadId).toBeNull()
  expect(browser.transferring).toBe(false)
})

it('automatically chooses pending file fields and offers an explicit retry without model arguments', async () => {
  await open()
  const file = new File(['content'], 'example.txt')
  api.post.mockResolvedValueOnce({ data: { uploadId: 'used', chosen: true } })
  await browser.transfer('upload', file)
  expect(browser.uploadId).toBeNull()
  expect(await browser.transfer('choose')).toBeNull()
  expect(api.post).toHaveBeenCalledTimes(1)
  api.post.mockResolvedValueOnce({ data: { uploadId: 'staged', chosen: false } })
  await browser.transfer('upload', file)
  expect(browser.uploadId).toBe('staged')
  api.post.mockRejectedValueOnce(new Error('No pending file chooser'))
  await expect(browser.transfer('choose')).rejects.toThrow('No pending file chooser')
  expect(browser.uploadId).toBe('staged')
  expect(browser.manual).toBe(true)
  api.post.mockResolvedValueOnce({ data: { ok: true } })
  await browser.transfer('choose')
  expect(api.post).toHaveBeenLastCalledWith('/browsers/browser-1/upload/staged/choose', null, transferConfig())
  expect(browser.uploadId).toBeNull()
})

it('ignores a late choose response instead of clearing a new connection upload', async () => {
  await open()
  browser.uploadId = 'old'
  let finish
  api.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const pending = browser.transfer('choose')
  expect(browser.manual).toBe(false)
  await open()
  browser.uploadId = 'new'
  finish({ data: { ok: true } })
  expect(await pending).toBeNull()
  expect(browser.uploadId).toBe('new')
})

it('skips unavailable browser adapters and suppresses only expected catalog-race errors', async () => {
  const integrations = useIntegrationStore()
  integrations.catalogLoaded = true
  integrations.catalog = [{ adapterType: 'browser', availability: 'disabled', personalOnly: true }]
  integrations.browser = { configured: true, instances: [instance] }
  expect(await integrations.fetchBrowser()).toEqual({ configured: false, instances: [] })
  expect(api.get).not.toHaveBeenCalled()
  expect(integrations.browser.instances).toEqual([])
  integrations.catalogLoaded = false
  for (const code of ['integration.adapterDisabled', 'integration.personalAdapterDisabled']) {
    api.get.mockRejectedValueOnce({ response: { data: { error: { code } } } })
    await expect(integrations.fetchBrowser()).resolves.toEqual({ configured: false, instances: [] })
  }
  expect(api.get).toHaveBeenLastCalledWith('/integrations/browser', {
    params: { scope: 'personal' }, suppressErrorToast: true
  })
  expect(notifyError).not.toHaveBeenCalled()
  const error = { response: { status: 503, data: { error: { code: 'browser.unavailable' } } } }
  api.get.mockRejectedValueOnce(error)
  await expect(integrations.fetchBrowser()).rejects.toBe(error)
  expect(notifyError).toHaveBeenCalledExactlyOnceWith(error)
})

it('distinguishes an unloaded settings-scope browser cache from authoritative browser responses', async () => {
  const integrations = useIntegrationStore()
  integrations.catalogLoaded = true
  integrations.catalog = [{ adapterType: 'browser', availability: 'full', personalOnly: true }]
  api.get.mockResolvedValueOnce({ data: { configured: true, instances: [instance] } })
  await integrations.fetchBrowser()
  expect(integrations.browserLoaded).toBe(true)
  let finish
  api.get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const stale = integrations.fetchBrowser()
  integrations.selectScope('personal')
  expect(integrations.browserLoaded).toBe(false)
  expect(integrations.browser.instances).toEqual([])
  finish({ data: { configured: true, instances: [instance] } })
  await stale
  expect(integrations.browserLoaded).toBe(false)
  api.get.mockRejectedValueOnce(new Error('Temporary fetch failure'))
  await expect(integrations.fetchBrowser()).rejects.toThrow('Temporary fetch failure')
  expect(integrations.browserLoaded).toBe(false)
  integrations.catalogLoadFailed = true
  api.get.mockResolvedValueOnce({ data: { configured: true, instances: [instance] } })
  await integrations.fetchBrowser()
  expect(integrations.browserLoaded).toBe(true)
  expect(integrations.browser.instances).toEqual([instance])
  integrations.catalogLoadFailed = false
  api.get.mockResolvedValueOnce({ data: { configured: true, instances: [{ ...instance, enabled: false }] } })
  await integrations.fetchBrowser()
  expect(integrations.browserLoaded).toBe(true)
  expect(integrations.browser.instances[0].enabled).toBe(false)
  api.get.mockResolvedValueOnce({ data: { configured: false, instances: [] } })
  await integrations.fetchBrowser()
  expect(integrations.browserLoaded).toBe(true)
  expect(integrations.browser.instances).toEqual([])
})

it('opens setup independently of any session without starting and guards old output/input callbacks', () => {
  const setup = useBrowserSetupStore()
  setup.connect('browser-1')
  const old = FakeWebSocket.instances.at(-1)
  expect(old.url).toContain('/api/browsers/browser-1/setup')
  expect(old.url).not.toContain('sessionId')
  old.open()
  expect(old.send.mock.calls).toEqual([['{"type":"status"}']])
  setup.start()
  expect(old.send).toHaveBeenLastCalledWith('{"type":"start"}')
  old.message({ type: 'state', state: 'installing', setupId: 'setup-1' })
  setup.input('Grüße 世界')
  const input = JSON.parse(old.send.mock.calls.at(-1)[0])
  expect(new TextDecoder().decode(Uint8Array.from(atob(input.data), (char) => char.charCodeAt(0)))).toBe('Grüße 世界')
  const encoded = new TextEncoder().encode('世界')
  old.binary(encoded.subarray(0, 2))
  old.binary(encoded.subarray(2))
  expect(setup.buffer).toBe('世界')
  expect(useIntegrationStore().browserStatuses['browser-1'].state).toBe('installing')
  setup.release()
  setup.connect('browser-2')
  old.binary([65])
  old.message({ type: 'state', state: 'ready' })
  old.onclose()
  expect(setup.buffer).toBe('')
  expect(setup.status).toBeNull()
  expect(old.send.mock.calls.filter(([raw]) => JSON.parse(raw).type === 'close')).toHaveLength(0)
  const chunks = setupInputChunks('世'.repeat(30000))
  expect(chunks.every((chunk) => atob(chunk).length <= 65536)).toBe(true)
  expect(new TextDecoder().decode(Uint8Array.from(chunks.map(atob).join(''), (char) => char.charCodeAt(0)))).toBe('世'.repeat(30000))
})

it('stores browser.instances and uses personal CRUD/status/test/reset regardless of settings scope', async () => {
  const integrations = useIntegrationStore()
  const payload = { configured: true, instances: [instance] }
  const config = { params: { scope: 'personal' } }
  api.get.mockResolvedValue({ data: payload })
  api.post.mockResolvedValue({ data: payload })
  api.patch.mockResolvedValue({ data: payload })
  api.delete.mockResolvedValue({ data: { configured: false, instances: [] } })
  await integrations.fetchBrowser()
  expect(integrations.scope).toBe('instance')
  expect(integrations.browser).toEqual(payload)
  const settings = { name: 'Browser', executionAgentId: 'agent-1', enabled: true, config: {} }
  await integrations.saveBrowser(null, settings)
  expect(api.post).toHaveBeenCalledWith('/integrations/browser', settings, config)
  await integrations.saveBrowser('browser-1', settings)
  expect(api.patch).toHaveBeenCalledWith('/integrations/browser/browser-1', settings, config)
  api.get.mockResolvedValueOnce({ data: { state: 'missing' } })
  await integrations.browserStatus('browser-1')
  expect(integrations.browserStatuses['browser-1'].state).toBe('missing')
  await integrations.browserStatus('browser-1', true)
  expect(api.post).toHaveBeenCalledWith('/integrations/browser/browser-1/test', null, config)
  await integrations.resetBrowser('browser-1')
  expect(api.post).toHaveBeenCalledWith('/integrations/browser/browser-1/reset', null, config)
  await integrations.removeBrowser('browser-1')
  expect(integrations.browser.instances).toEqual([])
  expect(api.delete).toHaveBeenCalledWith('/integrations/browser/browser-1', config)
})

it('gates the launcher on the existing global catalog, configured enabled instance and connected agent', () => {
  const integrations = useIntegrationStore()
  const session = { id: 'session-1', preferredExecutionAgentId: 'agent-1' }
  const agent = { connected: true }
  const agents = { byId: () => agent }
  integrations.browser = { configured: true, instances: [instance] }
  expect(browserControlCenterAvailable(session, agents, integrations)).toBe(false)
  integrations.catalog = [{ adapterType: 'browser', availability: 'full', personalOnly: true }]
  expect(browserControlCenterAvailable(session, agents, integrations)).toBe(true)
  expect(integrations.adapterAvailable('browser', 'instance')).toBe(false)
  agent.connected = false
  expect(browserControlCenterAvailable(session, agents, integrations)).toBe(false)
  agent.connected = true
  integrations.browser.instances[0] = { ...instance, enabled: false }
  expect(browserControlCenterAvailable(session, agents, integrations)).toBe(false)
})

it('translates all browser error codes safely and never equates configuration with readiness', () => {
  const codes = ['unavailable', 'notReady', 'busy', 'notConnected', 'bindingChanged', 'setupFailed',
    'dependenciesMissing', 'unsupported', 'sandboxUnavailable', 'actionFailed', 'invalidTarget',
    'fileTooLarge', 'profileOwnerMismatch', 'outcomeUnknown', 'controlTimeout']
  for (const code of codes) {
    const message = apiErrorMessage({ error: { code: `browser.${code}`, message: 'secret provider error' } })
    expect(message).not.toBe('Die Aktion ist fehlgeschlagen.')
    expect(message).not.toContain('secret')
  }
  expect(browserStatusLabel({ configured: true })).toBe('Browserstatus noch nicht geprüft.')
  expect(browserStatusLabel({ state: 'missing' })).toContain('eingerichtet')
  expect(browserSocketUrl('id', '/setup', {}, { protocol: 'https:', host: 'chat.example' })).toBe('wss://chat.example/api/browsers/id/setup')
})
