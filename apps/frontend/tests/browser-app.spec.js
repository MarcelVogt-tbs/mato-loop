// @vitest-environment jsdom
import { createApp, h, markRaw, nextTick, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { Quasar, QAvatar, QBtnToggle, QForm, QSkeleton, QBanner, QBtn, QCard, QCardActions, QCardSection, QDialog, QFile, QIcon, QInput,
  QItem, QItemLabel, QItemSection, QLinearProgress, QList, QMenu, QSelect, QSpace, QTab, QTabs, QToggle, QToolbar, QToolbarTitle, QTooltip, QSeparator, QScrollArea, ClosePopup, Notify, Ripple } from 'quasar'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import BrowserApp from '../src/components/BrowserApp.vue'
import BrowserFooter from '../src/components/BrowserFooter.vue'
import BrowserSetupDialog from '../src/components/BrowserSetupDialog.vue'
import IntegrationAccessPage from '../src/pages/settings/IntegrationAccessPage.vue'
import AgentCodingApp from '../src/components/AgentCodingApp.vue'
import { useBrowserStore } from '../src/stores/browser'
import { useIntegrationStore } from '../src/stores/integrations'
import { useControlCenterStore } from '../src/stores/control-center'
import { useChatStore } from 'stores/chat'
import { FakeWebSocket } from './terminal-websocket'
import KeyboardModule from '@novnc/novnc/lib/input/keyboard.js'
import { notifyError } from 'src/utils/notifications'

const { agentOperations, api, confirm, readText, writeText } = vi.hoisted(() => ({
  agentOperations: [], api: { get: vi.fn(), put: vi.fn(), post: vi.fn(), patch: vi.fn() }, confirm: vi.fn(), readText: vi.fn(), writeText: vi.fn()
}))
vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api }))
vi.mock('src/utils/notifications', () => ({ notifyError: vi.fn() }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm }) }))
vi.mock('stores/chat', async () => {
  const { reactive } = await import('vue')
  const chat = reactive({ current: null })
  return { useChatStore: () => chat }
})
vi.mock('stores/execution-agents', () => ({ useExecutionAgentStore: () => ({ byId: () => ({
  id: 'agent-1', connected: true, homeDirectory: '/home/user', operations: agentOperations
}) }) }))
vi.mock('@xterm/xterm', () => ({ Terminal: class {
  static strings = {}
  constructor() { this.options = {}; this.cols = 80; this.rows = 24 }
  loadAddon() {} open() {} onData() {} dispose() {} reset() {}
  write(data, callback) { callback?.() }
} }))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }))

const components = { QAvatar, QBtnToggle, QForm, QSkeleton, QBanner, QBtn, QCard, QCardActions, QCardSection, QDialog, QFile, QIcon, QInput,
  QItem, QItemLabel, QItemSection, QLinearProgress, QList, QMenu, QSelect, QSpace, QTab, QTabs, QToggle, QToolbar, QToolbarTitle, QTooltip, QSeparator, QScrollArea }
const viewId = '00000000-0000-4000-8000-000000000001'
const originalScrollIntoView = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView')
const transferConfig = () => ({ params: { browserId: 'runtime-1', sessionId: 'session-1', viewId }, signal: expect.any(AbortSignal), suppressErrorToast: true })
let app, root, pinia, browser, keyboard, router
let observers
beforeEach(() => {
  vi.clearAllMocks()
  agentOperations.splice(0, Infinity, 'filesystem.list', 'filesystem.import', 'integration.browser.execute', 'integration.browser.view')
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
  pinia = createPinia()
  setActivePinia(pinia)
  browser = useBrowserStore()
  useChatStore().current = { id: 'session-1', preferredExecutionAgentId: 'agent-1' }
  browser.agentId = 'agent-1'
  vi.spyOn(browser, 'mountView').mockImplementation(async (host) => {
    browser.viewParent = host
    browser.viewVisible = true
    if (browser.viewHost) host.replaceChildren(browser.viewHost)
  })
  useControlCenterStore().syncSession({ id: 'session-1', controlCenter: {
    tabs: [{ appId: 'browser' }], activeAppId: 'browser', open: true
  } })
  const integrations = useIntegrationStore()
  integrations.catalog = [{ adapterType: 'browser', availability: 'full', personalOnly: true }]
  integrations.browser = { configured: true, instances: [{ id: 'browser-1', name: 'Browser', enabled: true, executionAgentId: 'agent-1', config: {} }] }
  api.get.mockResolvedValue({ data: { state: 'missing' } })
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { readText, writeText } })
  vi.stubGlobal('WebSocket', FakeWebSocket)
  observers = []
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback) { this.callback = callback; observers.push(this) }
    observe(host) { this.host = host }
    disconnect() {}
  })
  vi.spyOn(Element.prototype, 'getClientRects').mockReturnValue([{ width: 1024, height: 768 }])
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  FakeWebSocket.instances = []
})
afterEach(() => {
  keyboard?.ungrab()
  keyboard = null
  app?.unmount()
  root?.remove()
  browser.release()
  if (originalScrollIntoView) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', originalScrollIntoView)
  else delete HTMLElement.prototype.scrollIntoView
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})
function button(label) { return [...document.querySelectorAll('button')].find((element) => element.getAttribute('aria-label') === label || element.textContent.includes(label)) }
function menuItem(label) { return [...document.querySelectorAll('.q-menu .q-item')].find((element) => element.textContent.includes(label)) }
async function fileAction(label) {
  if (button(label)) { button(label).click(); return }
  await vi.waitFor(() => expect(document.querySelector('.q-menu')).toBeNull())
  button('Weitere Browseraktionen').click()
  await vi.waitFor(() => expect(menuItem(label)).toBeDefined())
  menuItem(label).click()
}
async function mount(component, { width = 1440, dark = false, path = '/?session=session-1' } = {}) {
  router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', name: 'chat', component: { render: () => null } },
    { path: '/settings/integrations', name: 'settings-integrations', component: { render: () => null } },
    { path: '/settings/integrations/:integrationId/access', name: 'settings-integration-access', component: IntegrationAccessPage }
  ] })
  await router.push(path)
  root = document.createElement('div')
  document.body.append(root)
  app = createApp(component)
  app.use(pinia).use(router).use(Quasar, { components, directives: { ClosePopup, Ripple }, plugins: { Notify }, config: { dark } })
  app.config.globalProperties.$q.dark.set(dark)
  // DOM behavior only: jsdom does not measure responsive layout or render pixels.
  app.config.globalProperties.$q.screen.lt.sm = width < 600
  app.mount(root)
  await nextTick()
  await nextTick()
}

async function liveViewer() {
  await mount(BrowserApp)
  const socket = browser.socket
  socket.open()
  socket.message({ type: 'state', browserId: 'runtime-1', viewId, canAttach: true, attached: false, busy: false })
  browser.rfb = markRaw(Object.assign(new EventTarget(), { sendKey: vi.fn(), disconnect: vi.fn() }))
  browser.viewStatus = 'open'
  await nextTick()
  await nextTick()
  const canvas = document.createElement('canvas')
  canvas.tabIndex = 0
  root.querySelector('.browser-app__view').append(canvas)
  canvas.focus()
  const Keyboard = KeyboardModule.default || KeyboardModule
  keyboard = new Keyboard(canvas)
  keyboard.onkeyevent = (...args) => browser.rfb?.sendKey(...args)
  keyboard.grab()
  return canvas
}

function key(canvas, type, key, code, ctrlKey = true) {
  const event = new KeyboardEvent(type, { key, code, ctrlKey, bubbles: true, cancelable: true })
  canvas.dispatchEvent(event)
  return event
}

const navigation = { type: 'navigation', browserId: 'runtime-1', tabs: [
  { id: 'tab-1', title: 'Erste Seite', url: 'https://example.com/', loading: false },
  { id: 'tab-2', title: 'Zweite Seite', url: 'https://second.example/', loading: false }
], activeTabId: 'tab-1', canGoBack: true, canGoForward: false, loading: false, nativeChrome: false }

it('shows a background state without opening a viewer for a headless agent', async () => {
  agentOperations.splice(agentOperations.indexOf('integration.browser.view'), 1)
  await mount(BrowserApp)
  expect(root.textContent).toContain('Browser läuft im Hintergrund')
  expect(FakeWebSocket.instances).toHaveLength(0)
  expect(browser.mountView).not.toHaveBeenCalled()
})

it.each([{ width: 390, dark: false }, { width: 390, dark: true }, { width: 1440, dark: false }, { width: 1440, dark: true }])(
  'shows one explicit takeover action, progress and quiet replacement without reclaim (%j)', async (viewport) => {
    const shown = ref(true)
    await mount({ render: () => shown.value ? h(BrowserApp) : null }, viewport)
    const socket = browser.socket
    socket.open()
    socket.message({ type: 'availability', status: 'waiting', canTakeover: false })
    await nextTick()
    expect(root.textContent).toContain('gerade geöffnet oder geschlossen')
    expect(button('Browser hier übernehmen')).toBeUndefined()
    socket.message({ type: 'availability', status: 'occupied', browserId: 'runtime-1', viewId: 'offered-view',
      ownerSession: { id: 'session-2', title: '<b>Andere Sitzung</b>' }, busy: true, canTakeover: true })
    await nextTick()
    expect(root.querySelector('.app-state-message')).not.toBeNull()
    expect(root.querySelector('.q-banner')).toBeNull()
    expect(root.querySelector('.browser-app__view').style.display).toBe('none')
    expect(root.querySelector('[aria-label="Webseiten"]')).toBeNull()
    expect(root.querySelector('[aria-label="Browsersteuerung"]')).toBeNull()
    expect(root.textContent).toContain('In einer anderen Ansicht geöffnet')
    expect(root.textContent).toContain('<b>Andere Sitzung</b>')
    expect(root.querySelector('b')).toBeNull()
    expect([...root.querySelectorAll('button')].filter((item) => item.textContent === 'Browser hier übernehmen')).toHaveLength(1)
    expect(root.querySelector('[aria-label="Browser wird verbunden"]')).toBeNull()
    expect(button('Neuer Tab')).toBeUndefined()
    expect(root.querySelector('input[aria-label="Webadresse"]')).toBeNull()
    expect(browser.mountView).not.toHaveBeenCalled()
    Element.prototype.getClientRects.mockReturnValue([])
    button('Browser hier übernehmen').click()
    await nextTick()
    expect(socket.send).toHaveBeenCalledExactlyOnceWith('{"type":"takeover","browserId":"runtime-1","viewId":"offered-view"}')
    expect(root.textContent).toContain('Browser wird hier übernommen')
    expect(root.querySelector('[aria-label="Browser wird hier übernommen"]')).not.toBeNull()
    expect(button('Browser hier übernehmen')).toBeUndefined()
    expect(confirm).not.toHaveBeenCalled()
    socket.message({ type: 'state', browserId: 'runtime-1', viewId, attached: false, busy: false, canAttach: true })
    browser.viewStatus = 'open'
    await nextTick()
    expect(browser.viewId).toBe(viewId)
    expect(socket.send).not.toHaveBeenCalledWith('{"type":"attach"}')
    socket.message({ type: 'replaced' })
    await nextTick()
    expect(root.textContent).toContain('in einer anderen Ansicht übernommen')
    expect(root.querySelector('[role="progressbar"]')).toBeNull()
    expect(button('Neu verbinden')).toBeDefined()
    shown.value = false
    await nextTick()
    shown.value = true
    await nextTick()
    useControlCenterStore().open = false
    await nextTick()
    useControlCenterStore().open = true
    document.dispatchEvent(new Event('visibilitychange'))
    window.dispatchEvent(new Event('focus'))
    await nextTick()
    expect(browser.status).toBe('replaced')
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(notifyError).not.toHaveBeenCalled()
    button('Neu verbinden').click()
    await nextTick()
    expect(FakeWebSocket.instances).toHaveLength(2)
  }
)

it('closes Chromium only from the explicit Browser menu action', async () => {
  await liveViewer()
  const socket = browser.socket
  button('Weitere Browseraktionen').click()
  await vi.waitFor(() => expect(menuItem('Browser schließen')).toBeDefined())
  menuItem('Browser schließen').click()
  await nextTick()
  expect(socket.send.mock.calls).toEqual([['{"type":"close"}'], ['{"type":"release"}']])
  useControlCenterStore().open = false
  await nextTick()
  useControlCenterStore().open = true
  await nextTick()
  expect(FakeWebSocket.instances).toHaveLength(1)
})

it.each([{ width: 390, dark: false }, { width: 390, dark: true }, { width: 1440, dark: false }, { width: 1440, dark: true }])(
  'parks an unmounted A viewer, shows no A page or attachment in B, and reuses it on return (%j)', async (viewport) => {
    const shown = ref(true)
    await mount({ render: () => shown.value ? h('div', [h(BrowserApp), h(BrowserFooter)]) : null }, viewport)
    const original = browser.socket
    original.open()
    original.message({ type: 'state', browserId: 'runtime-1', viewId, attached: true, busy: false, canAttach: true })
    original.message(navigation)
    const viewHost = document.createElement('div')
    viewHost.append(document.createElement('canvas'))
    browser.viewHost = markRaw(viewHost)
    const rfb = markRaw({ disconnect: vi.fn(), sendKey: vi.fn(), viewOnly: true })
    browser.rfb = rfb
    browser.viewStatus = 'open'
    await nextTick()
    await nextTick()
    expect(root.querySelector('canvas')).not.toBeNull()
    shown.value = false
    await nextTick()
    expect(browser.viewVisible).toBe(false)
    expect(viewHost.parentElement).toBeNull()
    expect(rfb.disconnect).not.toHaveBeenCalled()
    useChatStore().current = { id: 'session-2', preferredExecutionAgentId: 'agent-1' }
    const center = useControlCenterStore()
    center.syncSession({ id: 'session-2', controlCenter: { tabs: [{ appId: 'browser' }], activeAppId: 'browser', open: true } })
    await router.push('/?session=session-2')
    shown.value = true
    await nextTick()
    await nextTick()
    expect(root.querySelector('canvas')).toBeNull()
    expect(root.querySelectorAll('[role="tab"]')).toHaveLength(0)
    expect(root.querySelector('input[aria-label="Webadresse"]')).toBeNull()
    expect(root.querySelector('[role="switch"][aria-label="Agent verbinden"]').getAttribute('aria-checked')).toBe('false')
    expect(root.querySelector('[role="switch"][aria-label="Agent verbinden"]').getAttribute('aria-disabled')).toBe('true')
    expect(button('Manuell übernehmen')).toBeUndefined()
    expect(button('Browser hier übernehmen')).toBeDefined()
    expect(root.textContent).not.toContain('Erste Seite')
    expect(browser.mountView.mock.calls.at(-1)[0]).not.toBe(root.querySelector('.browser-app__view'))
    vi.useFakeTimers()
    await vi.advanceTimersByTimeAsync(65000)
    expect(original.close).not.toHaveBeenCalled()
    expect(rfb.disconnect).not.toHaveBeenCalled()
    expect(browser.attached).toBe(true)
    expect(FakeWebSocket.instances).toHaveLength(1)
    useChatStore().current = { id: 'session-1', preferredExecutionAgentId: 'agent-1' }
    center.syncSession({ id: 'session-1', controlCenter: { tabs: [{ appId: 'browser' }], activeAppId: 'browser', open: true } })
    await router.push('/?session=session-1')
    await nextTick()
    await nextTick()
    expect(root.querySelector('canvas')).not.toBeNull()
    expect(root.querySelector('input[aria-label="Webadresse"]').value).toBe('https://example.com/')
    expect(root.querySelector('[role="switch"]').getAttribute('aria-checked')).toBe('true')
    expect(browser.socket).toBe(original)
    expect(browser.rfb).toBe(rfb)
    expect(FakeWebSocket.instances).toHaveLength(1)
  }
)

it('releases native noVNC held keys before parking locks input', async () => {
  const canvas = await liveViewer()
  browser.rfb._keyboard = keyboard
  key(canvas, 'keydown', 'Shift', 'ShiftLeft', false)
  key(canvas, 'keydown', 'x', 'KeyX', false)
  browser.setDisplayedSession('session-2')
  expect(browser.rfb.sendKey.mock.calls.map((args) => args.slice(0, 3))).toEqual([
    [0xffe1, 'ShiftLeft', true], [120, 'KeyX', true], [0xffe1, 'ShiftLeft', false], [120, 'KeyX', false]
  ])
  expect(browser.rfb.viewOnly).toBe(true)
  key(canvas, 'keydown', 'z', 'KeyZ', false)
  expect(browser.rfb.sendKey).toHaveBeenCalledTimes(4)
})

it('adds authorized Browser requests without opening the Control Center and connects in background', async () => {
  const center = useControlCenterStore()
  const session = { id: 'session-1', controlCenter: { tabs: [{ appId: 'files' }], activeAppId: 'files', open: true,
    fileViews: { session: { scope: 'session', selectedPath: 'notes.txt', selectedKind: 'file', rootIdentity: 'session-1' } } } }
  center.syncSession(session)
  await mount(BrowserApp)
  const saves = []
  api.patch.mockImplementation((path, body) => new Promise((resolve) => { saves.push({ path, body, resolve }) }))
  const previous = center.setOpen(false)
  const request = { id: 'request-1', sessionId: 'session-1', agentId: 'agent-1', integrationId: 'browser-1', runId: 'run-1' }
  api.get.mockResolvedValue({ data: { request } })
  await browser.reconcileControlRequest('session-1')
  await nextTick()
  expect(saves).toHaveLength(1)
  expect(FakeWebSocket.instances).toHaveLength(1)
  saves[0].resolve({ data: { controlCenter: { ...session.controlCenter, open: false } } })
  await vi.waitFor(() => expect(saves).toHaveLength(2))
  expect(saves[1].path).toBe('/sessions/session-1')
  expect(saves[1].body.control_center).toMatchObject({ active_app_id: 'files', open: false,
    file_views: { session: { selected_path: 'notes.txt' } } })
  expect(FakeWebSocket.instances).toHaveLength(1)
  saves[1].resolve({ data: { controlCenter: { ...session.controlCenter, tabs: [{ appId: 'files' }, { appId: 'browser' }], activeAppId: 'files', open: false } } })
  await previous
  await nextTick()
  expect(FakeWebSocket.instances).toHaveLength(1)
  expect(browser.socket.url).toContain('controlRequestId=request-1')
  expect(router.currentRoute.value.query.session).toBe('session-1')
  expect(center.confirmedState.fileViews.session.selectedPath).toBe('notes.txt')
  await browser.reconcileControlRequest('session-1')
  await nextTick()
  expect(FakeWebSocket.instances).toHaveLength(1)
  expect(api.patch).toHaveBeenCalledTimes(2)
})

it('offers manual cancellation while requested attachment is still in flight', async () => {
  await mount({ render: () => h('div', [h(BrowserApp), h(BrowserFooter)]) })
  const socket = browser.socket
  socket.open()
  socket.message({ type: 'state', browserId: 'runtime-1', viewId, attached: false, canAttach: true })
  Object.assign(browser, { viewStatus: 'open', controlRequestId: 'request-1', requestedAttachSent: true, submitting: 'attach' })
  await nextTick()
  expect(button('Manuell übernehmen').disabled).toBe(false)
  button('Manuell übernehmen').click()
  expect(socket.send).toHaveBeenCalledExactlyOnceWith('{"type":"detach"}')
  expect(browser.controlRequestId).toBeNull()
  expect(browser.submitting).toBe('detach')
})

it.each([{ width: 390, dark: false }, { width: 390, dark: true }, { width: 1440, dark: false }, { width: 1440, dark: true }])(
  'renders server tabs and navigation without optimistic URLs, duplicate attachment, or mobile action loss (%j)', async (viewport) => {
    await mount({ render: () => h('div', [h(BrowserApp), h(BrowserFooter)]) }, viewport)
    const socket = browser.socket
    socket.open()
    socket.message({ type: 'state', browserId: 'runtime-1', viewId, canAttach: true, attached: false, busy: false })
    browser.viewStatus = 'open'
    await nextTick()
    expect(button('Neuer Tab').disabled).toBe(true)
    socket.message(navigation)
    await nextTick()
    const address = root.querySelector('input[aria-label="Webadresse"]')
    const tab = (name) => root.querySelector(`[role="tab"][aria-label="${name}"]`)
    expect(root.querySelectorAll('[role="tab"]')).toHaveLength(2)
    for (const title of ['Erste Seite', 'Zweite Seite']) {
      const shell = tab(title).parentElement
      expect(shell.classList.contains('app-closable-tab')).toBe(true)
      expect(shell.contains(button(`${title} schließen`))).toBe(true)
      expect(tab(title).contains(button(`${title} schließen`))).toBe(false)
    }
    expect(address.value).toBe('https://example.com/')
    expect(button('Zurück').disabled).toBe(false)
    expect(button('Vorwärts').disabled).toBe(true)
    tab('Zweite Seite').click()
    await nextTick()
    expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toEqual({ type: 'navigation', action: 'select', tabId: 'tab-2' })
    expect(tab('Erste Seite').getAttribute('aria-selected')).toBe('true')
    expect(tab('Erste Seite').parentElement.classList.contains('app-closable-tab--active')).toBe(true)
    expect(tab('Zweite Seite').parentElement.classList.contains('app-closable-tab--active')).toBe(false)
    expect(address.value).toBe('https://example.com/')
    expect(root.querySelector('[role="switch"]').getAttribute('aria-disabled')).toBe('true')
    expect(button('Neuer Tab').disabled).toBe(true)
    socket.message({ ...navigation, completed: false })
    await nextTick()
    expect(button('Neuer Tab').disabled).toBe(true)
    socket.message({ ...navigation, activeTabId: 'tab-2', completed: true })
    await nextTick()
    expect(tab('Zweite Seite').getAttribute('aria-selected')).toBe('true')
    expect(tab('Zweite Seite').parentElement.classList.contains('app-closable-tab--active')).toBe(true)
    expect(address.value).toBe('https://second.example/')
    address.focus()
    address.value = 'localhost:3000/draft'
    address.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    socket.message({ ...navigation, activeTabId: 'tab-1', loading: true })
    await nextTick()
    expect(address.value).toBe('localhost:3000/draft')
    expect(button('Laden stoppen')).toBeDefined()
    key(address, 'keydown', 'Enter', 'Enter', false)
    await nextTick()
    expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toEqual({ type: 'navigation', action: 'navigate', tabId: 'tab-2', url: 'http://localhost:3000/draft' })
    expect(address.value).toBe('https://example.com/')
    socket.message({ type: 'error', operation: 'navigation', error: { code: 'browser.invalidTarget' } })
    await nextTick()
    expect(address.readOnly).toBe(false)
    address.value = 'javascript:alert(1)'
    address.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const sends = socket.send.mock.calls.length
    key(address, 'keydown', 'Enter', 'Enter', false)
    await nextTick()
    expect(socket.send).toHaveBeenCalledTimes(sends)
    expect(notifyError).toHaveBeenLastCalledWith({ error: { code: 'browser.invalidTarget' } })
    expect(address.value).toBe('https://example.com/')
    address.blur()
    button('Laden stoppen').click()
    expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toEqual({ type: 'navigation', action: 'stop', tabId: 'tab-1' })
    socket.message({ ...navigation, completed: true })
    await nextTick()
    button('Neuer Tab').click()
    expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toEqual({ type: 'navigation', action: 'new' })
    socket.message({ ...navigation, completed: true })
    await nextTick()
    const beforeClose = socket.send.mock.calls.length
    button('Zweite Seite schließen').click()
    expect(socket.send).toHaveBeenCalledTimes(beforeClose + 1)
    expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toEqual({ type: 'navigation', action: 'close', tabId: 'tab-2' })
    socket.message({ ...navigation, completed: true })
    await nextTick()
    button('Weitere Browseraktionen').click()
    await vi.waitFor(() => expect(menuItem('Browseroberfläche anzeigen')).toBeDefined())
    const chrome = menuItem('Browseroberfläche anzeigen').querySelector('[role="switch"]')
    chrome.click()
    await nextTick()
    expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toEqual({ type: 'navigation', action: 'chrome', nativeChrome: true })
    expect(chrome.getAttribute('aria-checked')).toBe('false')
    socket.message({ ...navigation, nativeChrome: true, completed: true })
    await nextTick()
    expect(chrome.getAttribute('aria-checked')).toBe('true')
    expect(browser.navigationPending).toBe(false)
    chrome.click()
    await nextTick()
    expect(browser.navigationPending).toBe(true)
    // Observed native presentation can stay unchanged; completion still unlocks the UI.
    socket.message({ ...navigation, nativeChrome: true, completed: false })
    expect(browser.navigationPending).toBe(true)
    socket.message({ ...navigation, nativeChrome: true, completed: true })
    await nextTick()
    expect(browser.navigationPending).toBe(false)
    expect(chrome.getAttribute('aria-disabled')).not.toBe('true')
    if (viewport.width === 390) {
      expect(button('Text kopieren')).toBeUndefined()
      for (const label of ['Text kopieren', 'Text einfügen', 'Datei hochladen', 'Downloads']) expect(menuItem(label)).toBeDefined()
    }
    socket.message({ type: 'state', browserId: 'runtime-1', viewId, canAttach: true, attached: true, busy: false })
    socket.message({ ...navigation, activeTabId: 'tab-2', nativeChrome: true })
    await nextTick()
    expect(address.value).toBe('https://second.example/')
    expect(address.readOnly).toBe(true)
    expect(address.disabled).toBe(false)
    expect(button('Neuer Tab').disabled).toBe(true)
    expect(tab('Zweite Seite').getAttribute('aria-disabled')).toBe('true')
    expect(button('Zweite Seite schließen').disabled).toBe(true)
    const beforeDisabledClose = socket.send.mock.calls.length
    button('Zweite Seite schließen').click()
    expect(socket.send).toHaveBeenCalledTimes(beforeDisabledClose)
    expect(chrome.getAttribute('aria-disabled')).toBe('true')
    if (viewport.width === 390) expect(menuItem('Text einfügen').getAttribute('aria-disabled')).toBe('true')
    expect(socket.send.mock.calls.map(([raw]) => JSON.parse(raw).type)).not.toContain('attach')
    expect(socket.close).not.toHaveBeenCalled()
    expect(FakeWebSocket.instances).toHaveLength(1)
  }
)

it('navigates browser tab shells without selecting and restores focus after closing a focused tab', async () => {
  await liveViewer()
  const socket = browser.socket
  socket.message(navigation)
  await nextTick()
  const tabs = [...root.querySelectorAll('[role="tab"]')]
  tabs[0].focus()
  for (const [name, index] of [['ArrowLeft', 0], ['ArrowRight', 1], ['ArrowRight', 1], ['Home', 0], ['End', 1], ['ArrowLeft', 0]]) {
    expect(key(document.activeElement, 'keydown', name, name, false).defaultPrevented).toBe(true)
    await nextTick()
    expect(document.activeElement).toBe(tabs[index])
  }
  const close = button('Zweite Seite schließen')
  close.focus()
  expect(key(close, 'keydown', 'Home', 'Home', false).defaultPrevented).toBe(false)
  expect(document.activeElement).toBe(close)
  expect(socket.send).not.toHaveBeenCalled()
  close.click()
  expect(socket.send).toHaveBeenCalledExactlyOnceWith(JSON.stringify({ type: 'navigation', action: 'close', tabId: 'tab-2' }))
  socket.message({ ...navigation, tabs: [navigation.tabs[0]], completed: true })
  await nextTick()
  await nextTick()
  expect(document.activeElement).toBe(tabs[0])
})

it('captures Ctrl+L before noVNC only in the visible manual Browser and leaves local clipboard/input alone', async () => {
  const canvas = await liveViewer()
  browser.socket.message(navigation)
  await nextTick()
  const address = root.querySelector('input[aria-label="Webadresse"]')
  key(canvas, 'keydown', 'Control', 'ControlLeft')
  expect(key(canvas, 'keydown', 'l', 'KeyL').defaultPrevented).toBe(true)
  await nextTick()
  expect(document.activeElement).toBe(address)
  expect(address.selectionStart).toBe(0)
  expect(address.selectionEnd).toBe(address.value.length)
  expect(browser.controlKeys).toEqual([])
  expect(browser.rfb.sendKey.mock.calls).toEqual([[0xffe3, 'ControlLeft', true], [0xffe3, 'ControlLeft', false]])
  for (const letter of ['c', 'v']) expect(key(address, 'keydown', letter, `Key${letter.toUpperCase()}`).defaultPrevented).toBe(false)
  const paste = new Event('paste', { bubbles: true, cancelable: true })
  address.dispatchEvent(paste)
  expect(paste.defaultPrevented).toBe(false)
  expect(api.put).not.toHaveBeenCalled()
  expect(api.post).not.toHaveBeenCalled()
  expect(readText).not.toHaveBeenCalled()
  const other = document.createElement('input')
  root.querySelector('.browser-app').append(other)
  other.focus()
  expect(key(other, 'keydown', 'l', 'KeyL').defaultPrevented).toBe(false)
  expect(document.activeElement).toBe(other)
  other.remove()
  useControlCenterStore().open = false
  expect(key(address, 'keydown', 'l', 'KeyL').defaultPrevented).toBe(false)
  useControlCenterStore().open = true
  browser.attached = true
  expect(key(address, 'keydown', 'l', 'KeyL').defaultPrevented).toBe(false)
})

it.each(['canvas', 'address'])('handles Ctrl+T/W on the focused %s without queued navigation or native noVNC shortcuts', async (source) => {
  const canvas = await liveViewer()
  const socket = browser.socket
  socket.message(navigation)
  await nextTick()
  const target = source === 'canvas' ? canvas : root.querySelector('input[aria-label="Webadresse"]')
  target.focus()
  for (const [letter, action] of [['t', 'new'], ['w', 'close']]) {
    expect(key(target, 'keydown', letter, `Key${letter.toUpperCase()}`).defaultPrevented).toBe(true)
    expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toEqual({ type: 'navigation', action, ...(action === 'close' ? { tabId: 'tab-2' } : {}) })
    const count = socket.send.mock.calls.length
    const repeat = new KeyboardEvent('keydown', { key: letter, code: `Key${letter.toUpperCase()}`, ctrlKey: true, repeat: true, bubbles: true, cancelable: true })
    target.dispatchEvent(repeat)
    expect(repeat.defaultPrevented).toBe(true)
    expect(socket.send).toHaveBeenCalledTimes(count)
    key(target, 'keyup', letter, `Key${letter.toUpperCase()}`)
    socket.message({ ...navigation, activeTabId: 'tab-2', completed: true })
    await nextTick()
    if (source === 'address') expect(target.value).toBe('https://second.example/')
  }
  expect(browser.rfb.sendKey).not.toHaveBeenCalled()
  expect(socket.send).toHaveBeenCalledTimes(2)
  expect(socket.send).not.toHaveBeenCalledWith('{"type":"attach"}')
})

it('translates focused remote F12 to balanced Ctrl+Shift+I and leaves native Ctrl+Shift+I forwarded', async () => {
  const canvas = await liveViewer()
  expect(key(canvas, 'keydown', 'F12', 'F12', false).defaultPrevented).toBe(true)
  expect(key(canvas, 'keyup', 'F12', 'F12', false).defaultPrevented).toBe(true)
  expect(browser.rfb.sendKey.mock.calls).toEqual([
    [0xffe3, 'ControlLeft', true], [0xffe1, 'ShiftLeft', true], [0x49, 'KeyI', true],
    [0x49, 'KeyI', false], [0xffe1, 'ShiftLeft', false], [0xffe3, 'ControlLeft', false]
  ])
  browser.rfb.sendKey.mockClear()
  canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'F12', code: 'F12', repeat: true, bubbles: true, cancelable: true }))
  expect(browser.rfb.sendKey).not.toHaveBeenCalled()
  for (const type of ['keydown', 'keyup']) canvas.dispatchEvent(new KeyboardEvent(type, {
    key: 'I', code: 'KeyI', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true
  }))
  expect(browser.rfb.sendKey.mock.calls.map((args) => args.slice(0, 3))).toEqual([[0x49, 'KeyI', true], [0x49, 'KeyI', false]])
})

it('keeps browser shortcuts out of unrelated fields, toolbar controls, hidden views and model attachment', async () => {
  await liveViewer()
  browser.socket.message(navigation)
  await nextTick()
  const address = root.querySelector('input[aria-label="Webadresse"]')
  const other = document.createElement('input')
  root.querySelector('.browser-app').append(other)
  for (const target of [other, button('Weitere Browseraktionen')]) {
    target.focus()
    for (const letter of ['t', 'w']) expect(key(target, 'keydown', letter, `Key${letter.toUpperCase()}`).defaultPrevented).toBe(false)
    expect(key(target, 'keydown', 'F12', 'F12', false).defaultPrevented).toBe(false)
  }
  address.focus()
  expect(key(address, 'keydown', 'F12', 'F12', false).defaultPrevented).toBe(false)
  for (const blocked of ['attached', 'hidden']) {
    browser.attached = blocked === 'attached'
    useControlCenterStore().open = blocked !== 'hidden'
    for (const letter of ['t', 'w']) expect(key(address, 'keydown', letter, `Key${letter.toUpperCase()}`).defaultPrevented).toBe(false)
  }
  expect(browser.socket.send).not.toHaveBeenCalled()
  expect(browser.rfb.sendKey).not.toHaveBeenCalled()
})

it('starts only the visible persisted Browser tab once, never on hidden mount, status errors, or failed activation', async () => {
  const center = useControlCenterStore()
  center.activeAppId = 'session'
  center.confirmedState.activeAppId = 'session'
  await mount(BrowserApp)
  expect(FakeWebSocket.instances).toHaveLength(0)
  center.activeAppId = 'browser'
  await nextTick()
  expect(FakeWebSocket.instances).toHaveLength(0)
  // Failed optimistic selection rolls back without ever starting the browser.
  center.activeAppId = 'session'
  await nextTick()
  center.activeAppId = 'browser'
  center.confirmedState.activeAppId = 'browser'
  await nextTick()
  expect(FakeWebSocket.instances).toHaveLength(1)
  const socket = browser.socket
  socket.open()
  socket.message({ type: 'error', error: { code: 'browser.notReady' } })
  await nextTick()
  useIntegrationStore().browserStatuses['browser-1'] = { state: 'ready' }
  observers[0].callback([{ contentRect: { width: 1024, height: 768 } }])
  await nextTick()
  expect(FakeWebSocket.instances).toHaveLength(1)
  button('Browser erneut verbinden').click()
  await nextTick()
  expect(FakeWebSocket.instances).toHaveLength(2)
  expect(socket.send).not.toHaveBeenCalledWith('{"type":"attach"}')
})

it('ignores zero-size mounts and hidden resize observations, then reuses the live runtime on tab activation', async () => {
  const rects = vi.spyOn(Element.prototype, 'getClientRects').mockReturnValue([])
  await mount(BrowserApp)
  observers[0].callback([{ contentRect: { width: 0, height: 0 } }])
  expect(FakeWebSocket.instances).toHaveLength(0)
  rects.mockReturnValue([{ width: 390, height: 600 }])
  observers[0].callback([{ contentRect: { width: 390, height: 600 } }])
  const socket = browser.socket
  socket.open()
  socket.message({ type: 'state', browserId: 'runtime-1', viewId })
  browser.viewStatus = 'open'
  await nextTick()
  await nextTick()
  await vi.waitFor(() => expect(socket.send).toHaveBeenCalledWith('{"type":"resize","width":390,"height":600}'))
  socket.message({ type: 'resized', browserId: 'runtime-1', width: 390, height: 600 })
  useControlCenterStore().activeAppId = 'session'
  await nextTick()
  observers[0].callback([{ contentRect: { width: 500, height: 700 } }])
  expect(browser.resizeTarget).toEqual({ width: 390, height: 600 })
  useControlCenterStore().activeAppId = 'browser'
  await nextTick()
  expect(browser.socket).toBe(socket)
  expect(FakeWebSocket.instances).toHaveLength(1)
  expect(socket.close).not.toHaveBeenCalled()
})

it('remounts a retained viewer when its returning host becomes measurable', async () => {
  const rects = vi.spyOn(Element.prototype, 'getClientRects').mockReturnValue([])
  const viewHost = document.createElement('div')
  Object.assign(browser, {
    sessionId: 'session-1', integrationId: 'browser-1', browserId: 'runtime-1', viewId,
    socket: markRaw({ readyState: 1, send: vi.fn(), close: vi.fn() }), status: 'open', viewStatus: 'open',
    viewHost: markRaw(viewHost), viewVisible: false
  })
  await mount(BrowserApp)
  browser.mountView.mockClear()
  rects.mockReturnValue([{ width: 1024, height: 768 }])
  observers[0].callback([{ contentRect: { width: 1024, height: 768 } }])
  await vi.waitFor(() => expect(browser.mountView).toHaveBeenCalledWith(root.querySelector('.browser-app__view')))
  expect(browser.socket.readyState).toBe(1)
  expect(root.querySelector('.browser-app__view').firstChild).toBe(viewHost)
})

it.each(['closed', 'unconfirmed', 'draft'])('does not broaden activation gating for %s context', async (context) => {
  const center = useControlCenterStore()
  if (context === 'closed') center.open = false
  if (context === 'unconfirmed') center.confirmedState.open = false
  if (context === 'draft') center.sessionId = null
  await mount(BrowserApp)
  observers[0].callback([{ contentRect: { width: 390, height: 600 } }])
  expect(FakeWebSocket.instances).toHaveLength(0)
  expect(api.get).not.toHaveBeenCalled()
})

it('resets activation synchronously before a collapsed panel unmounts, but preserves it on a responsive remount', async () => {
  const shown = ref(true)
  await mount({ render: () => shown.value ? h(BrowserApp) : null })
  const center = useControlCenterStore()
  browser.release()
  shown.value = false
  await nextTick()
  shown.value = true
  await nextTick()
  expect(FakeWebSocket.instances).toHaveLength(1)
  center.open = false
  expect(browser.activationKey).toBeNull()
  shown.value = false
  await nextTick()
  center.open = true
  shown.value = true
  await nextTick()
  expect(FakeWebSocket.instances).toHaveLength(2)
})

it.each(['same', 'blur', 'hide', 'reset', 'attach', 'error'])('captures Ctrl+V before real noVNC, awaits UTF-8 PUT, and revalidates %s', async (change) => {
  const canvas = await liveViewer()
  key(canvas, 'keydown', 'Control', 'ControlLeft')
  expect(key(canvas, 'keydown', 'v', 'KeyV').defaultPrevented).toBe(false)
  expect(api.put).not.toHaveBeenCalled()
  expect(browser.rfb.sendKey.mock.calls).toEqual([[0xffe3, 'ControlLeft', true], [0xffe3, 'ControlLeft', false]])
  const event = new Event('paste', { bubbles: true, cancelable: true })
  canvas.dispatchEvent(event)
  expect(api.put).not.toHaveBeenCalled()
  let finish, fail
  api.put.mockImplementationOnce(() => new Promise((resolve, reject) => { finish = resolve; fail = reject }))
  const text = 'Grüße 世界 😀\nzweite\tZeile'
  // jsdom cannot synthesize isTrusted. Exercise the native handler separately;
  // the parent real-browser E2E supplies an actual OS/browser paste event.
  const pending = app._instance.setupState.nativePaste({
    isTrusted: true, preventDefault: vi.fn(), stopImmediatePropagation: vi.fn(),
    clipboardData: { files: [], types: ['text/plain'], getData: () => text }
  })
  expect(api.put).toHaveBeenCalledExactlyOnceWith('/browsers/browser-1/clipboard', { text }, transferConfig())
  const rfb = browser.rfb
  expect(rfb.sendKey).toHaveBeenCalledTimes(2)
  key(canvas, 'keyup', 'v', 'KeyV')
  key(canvas, 'keyup', 'Control', 'ControlLeft', false)
  if (change === 'blur') canvas.blur()
  if (change === 'hide') useControlCenterStore().activeAppId = 'session'
  if (change === 'reset') browser.viewGeneration += 1
  if (change === 'attach') browser.attached = true
  if (change === 'error') fail(new Error('PUT failed'))
  else finish({ data: { text } })
  await pending
  expect(rfb.sendKey.mock.calls.slice(2)).toEqual(change === 'same' ? [
    [0xffe3, 'ControlLeft', true], [118, 'KeyV', true], [118, 'KeyV', false], [0xffe3, 'ControlLeft', false]
  ] : [])
  expect(readText).not.toHaveBeenCalled()
})

it.each(['files', 'image', 'oversized'])('rejects %s native paste data without sending a chord', async (kind) => {
  await liveViewer()
  await app._instance.setupState.nativePaste({
    isTrusted: true, preventDefault: vi.fn(), stopImmediatePropagation: vi.fn(),
    clipboardData: { files: kind === 'files' ? [new File(['x'], 'x.txt')] : [],
      types: kind === 'image' ? ['image/png'] : ['text/plain'], getData: () => kind === 'oversized' ? '世'.repeat(6000) : '' }
  })
  expect(api.put).not.toHaveBeenCalled()
  expect(browser.rfb.sendKey).not.toHaveBeenCalled()
  await vi.waitFor(() => expect(document.body.textContent).toContain('Nur reiner Text kleiner als 16 KiB'))
})

it('preserves a physically held Ctrl after paste and leaves subsequent shortcuts to noVNC', async () => {
  const canvas = await liveViewer()
  key(canvas, 'keydown', 'Control', 'ControlRight')
  key(canvas, 'keydown', 'v', 'KeyV')
  api.put.mockResolvedValueOnce({ data: { text: 'text' } })
  await app._instance.setupState.nativePaste({
    isTrusted: true, preventDefault: vi.fn(), stopImmediatePropagation: vi.fn(),
    clipboardData: { files: [], types: ['text/plain'], getData: () => 'text' }
  })
  expect(browser.controlKeys).toEqual(['ControlRight'])
  expect(key(canvas, 'keydown', 'a', 'KeyA').defaultPrevented).toBe(true)
  expect(browser.rfb.sendKey.mock.calls.at(-1).slice(0, 3)).toEqual([97, 'KeyA', true])
  key(canvas, 'keyup', 'a', 'KeyA')
  key(canvas, 'keyup', 'Control', 'ControlRight', false)
  expect(browser.controlKeys).toEqual([])
  expect(browser.rfb.sendKey).toHaveBeenLastCalledWith(0xffe4, 'ControlRight', false)
})

it('starts confirmed copy POST and reserves clipboard activation synchronously without RFB copy or GET', async () => {
  const canvas = await liveViewer()
  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('ClipboardItem', class { constructor(data) { this.data = data } })
  const write = vi.fn(([item]) => item.data['text/plain'])
  navigator.clipboard.write = write
  const rfb = browser.rfb
  let finish
  api.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  key(canvas, 'keydown', 'c', 'KeyC')
  expect(api.post).toHaveBeenCalledExactlyOnceWith('/browsers/browser-1/clipboard/copy', null, transferConfig())
  expect(write).toHaveBeenCalledOnce()
  expect(write.mock.calls[0][0][0].data['text/plain']).toBeInstanceOf(Promise)
  expect(api.post.mock.invocationCallOrder[0]).toBeLessThan(write.mock.invocationCallOrder[0])
  expect(api.get).not.toHaveBeenCalled()
  expect(rfb.sendKey).not.toHaveBeenCalled()
  rfb.dispatchEvent(new CustomEvent('clipboard', { detail: { text: 'Ignore legacy events' } }))
  expect(browser.transferring).toBe(true)
  finish({ data: { text: '世界\nUTF-8' } })
  const blob = await write.mock.results[0].value
  expect(blob.type).toBe('text/plain')
  expect(blob.size).toBe(new TextEncoder().encode('世界\nUTF-8').length)
  expect(document.querySelector('textarea')).toBeNull()
  await nextTick()
  // Unchanged content still completes through the native copy acknowledgement.
  api.post.mockResolvedValueOnce({ data: { text: '世界\nUTF-8' } })
  key(canvas, 'keydown', 'c', 'KeyC')
  await vi.waitFor(() => expect(write).toHaveBeenCalledTimes(2))
  await write.mock.results[1].value
  expect(api.get).not.toHaveBeenCalled()
  expect(rfb.sendKey).not.toHaveBeenCalled()
})

it.each(['denied-early', 'denied-late', 'item-rejected', 'insecure', 'writeText-denied'])('retains confirmed text for local permission fallback without another read (%s)', async (reason) => {
  const canvas = await liveViewer()
  vi.stubGlobal('isSecureContext', reason !== 'insecure')
  vi.stubGlobal('ClipboardItem', reason === 'writeText-denied' ? undefined : class { constructor(data) {
    if (reason === 'item-rejected') throw new Error('Promise data unsupported')
    this.data = data
  } })
  navigator.clipboard.write = vi.fn(([item]) => reason === 'denied-early'
    ? Promise.reject(new Error('denied')) : item.data['text/plain'].then(() => { throw new Error('denied') }))
  if (reason === 'writeText-denied') writeText.mockRejectedValueOnce(new Error('denied'))
  let finish
  api.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  key(canvas, 'keydown', 'c', 'KeyC')
  await nextTick()
  expect(document.querySelector('textarea')).toBeNull()
  expect(writeText).not.toHaveBeenCalled()
  const text = 'Confirmed selection 世界\n'
  finish({ data: { text } })
  await vi.waitFor(() => expect(button('Lokal kopieren')?.disabled).toBe(false))
  expect(document.querySelector('textarea').value).toBe(text)
  expect(api.get).not.toHaveBeenCalled()
  expect(api.post).toHaveBeenCalledTimes(1)
  if (reason === 'writeText-denied') expect(writeText).toHaveBeenCalledExactlyOnceWith(text)
  else expect(writeText).not.toHaveBeenCalled()
  writeText.mockResolvedValueOnce()
  button('Lokal kopieren').click()
  await vi.waitFor(() => expect(writeText).toHaveBeenLastCalledWith(text))
})

it.each(['no-item', 'no-write'])('uses best-effort writeText only after confirmed data and revalidation (%s)', async (mode) => {
  const canvas = await liveViewer()
  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('ClipboardItem', mode === 'no-item' ? undefined : class {})
  let finish
  api.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  writeText.mockResolvedValueOnce()
  key(canvas, 'keydown', 'c', 'KeyC')
  expect(writeText).not.toHaveBeenCalled()
  finish({ data: { text: 'Best effort 世界' } })
  await vi.waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith('Best effort 世界'))
  expect(document.querySelector('textarea')).toBeNull()
  expect(api.get).not.toHaveBeenCalled()
})

it.each(['promise', 'writeText'])('discards confirmed copy after view/context/focus/attachment loss (%s)', async (mode) => {
  const canvas = await liveViewer()
  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('ClipboardItem', mode === 'promise' ? class { constructor(data) { this.data = data } } : undefined)
  const committed = vi.fn()
  navigator.clipboard.write = vi.fn(([item]) => item.data['text/plain'].then(committed))
  const initialGeneration = browser.viewGeneration
  for (const change of ['blur', 'hide', 'context', 'reset', 'attach', 'busy', 'disconnect']) {
    let finish
    api.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    const pending = app._instance.setupState.shortcutCopy()
    if (change === 'blur') { canvas.blur(); canvas.focus() }
    if (change === 'hide') { useControlCenterStore().open = false; useControlCenterStore().open = true }
    if (change === 'context') { useControlCenterStore().sessionId = null; useControlCenterStore().sessionId = 'session-1' }
    if (change === 'reset') { browser.viewGeneration += 1; browser.transferring = false }
    if (change === 'attach') { browser.attached = true; browser.attached = false }
    if (change === 'busy') { browser.busy = true; browser.busy = false }
    if (change === 'disconnect') browser.release()
    finish({ data: { text: 'Must not reach local clipboard' } })
    await pending
    expect(committed).not.toHaveBeenCalled()
    expect(writeText).not.toHaveBeenCalled()
    expect(document.querySelector('textarea')).toBeNull()
  }
  expect(initialGeneration).toBeLessThan(browser.viewGeneration)
  expect(api.post).toHaveBeenCalledTimes(7)
})

it('offers explicit GET after remote copy failure without duplicating the Axios toast', async () => {
  const canvas = await liveViewer()
  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('ClipboardItem', class { constructor(data) { this.data = data } })
  navigator.clipboard.write = vi.fn(([item]) => item.data['text/plain'])
  api.post.mockRejectedValueOnce(new Error('Axios failure'))
  key(canvas, 'keydown', 'c', 'KeyC')
  await vi.waitFor(() => expect(button('Browsertext lesen')).toBeDefined())
  expect(notifyError).toHaveBeenCalledTimes(1)
  expect(api.get).not.toHaveBeenCalled()
  expect(button('Lokal kopieren').disabled).toBe(true)
  api.get.mockResolvedValueOnce({ data: { text: 'Explicitly verified selection' } })
  button('Browsertext lesen').click()
  await vi.waitFor(() => expect(button('Lokal kopieren').disabled).toBe(false))
})

it.each([{ width: 390, dark: false }, { width: 390, dark: true }, { width: 1440, dark: false }, { width: 1440, dark: true }])(
  'requires explicit clipboard actions and offers German permission fallbacks (%j)', async (viewport) => {
    await mount({ render: () => h('div', [h(BrowserApp), h(BrowserFooter)]) }, viewport)
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(root.querySelector('[role="switch"]').getAttribute('aria-disabled')).toBe('true')
    expect(button('Browser öffnen')).toBeUndefined()
    const socket = FakeWebSocket.instances.at(-1)
    socket.open()
    socket.message({ type: 'state', browserId: 'runtime-1', viewId, canAttach: true, attached: false, busy: false })
    browser.viewStatus = 'open'
    await nextTick()
    expect(root.querySelectorAll('.q-toolbar')).toHaveLength(1)
    button('Weitere Browseraktionen').click()
    await vi.waitFor(() => expect(menuItem('Skalierung ausschalten')).toBeDefined())
    menuItem('Skalierung ausschalten').click()
    await nextTick()
    expect(browser.scaleViewport).toBe(false)
    await vi.waitFor(() => expect(menuItem('Ansicht einpassen')).toBeUndefined())
    button('Weitere Browseraktionen').click()
    await vi.waitFor(() => expect(menuItem('Ansicht einpassen')).toBeDefined())
    menuItem('Ansicht einpassen').click()
    await nextTick()
    expect(browser.scaleViewport).toBe(true)
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(socket.send).not.toHaveBeenCalled()
    api.get.mockResolvedValueOnce({ data: { text: 'Grüße 世界\nzweite\tZeile' } })
    expect(api.get.mock.calls.filter(([path]) => path.endsWith('/clipboard'))).toEqual([])
    await fileAction('Text kopieren')
    await vi.waitFor(() => expect(document.querySelector('textarea')?.value).toBe('Grüße 世界\nzweite\tZeile'))
    expect(api.get).toHaveBeenLastCalledWith('/browsers/browser-1/clipboard', transferConfig())
    expect(writeText).not.toHaveBeenCalled()
    writeText.mockRejectedValueOnce(new Error('denied'))
    button('Lokal kopieren').click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('manuell kopieren'))
    button('Schließen').click()
    await vi.waitFor(() => expect(document.querySelector('textarea')).toBeNull())
    await fileAction('Text einfügen')
    await vi.waitFor(() => expect(document.querySelector('textarea')).not.toBeNull())
    expect(document.body.textContent).toContain('im Kontextmenü des entfernten Browsers „Einfügen“')
    expect(readText).not.toHaveBeenCalled()
    readText.mockRejectedValueOnce(new Error('denied'))
    button('Lokal lesen').click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('manuell in das Feld einfügen'))
    const textarea = document.querySelector('textarea')
    textarea.value = '世界\nzweite\tZeile'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(button('Zwischenablage setzen').disabled).toBe(false)
    expect(button('Direkt eingeben')).toBeUndefined()
    api.put.mockResolvedValueOnce({ data: { text: textarea.value } })
    button('Zwischenablage setzen').click()
    await vi.waitFor(() => expect(api.put).toHaveBeenCalledExactlyOnceWith('/browsers/browser-1/clipboard', { text: '世界\nzweite\tZeile' }, transferConfig()))
    await vi.waitFor(() => expect(document.querySelector('textarea')).toBeNull())
    socket.message({ type: 'state', browserId: 'runtime-1', viewId, canAttach: false, attachReason: 'profileExcluded', attached: false, busy: false })
    await nextTick()
    expect(root.textContent).toContain('Integrationsauswahl im Agentenprofil')
    expect(root.querySelector('[role="switch"]').getAttribute('aria-disabled')).toBe('true')
    socket.message({ type: 'state', browserId: 'runtime-1', viewId, canAttach: true, attached: true, busy: true })
    await nextTick()
    expect(button('Manuell übernehmen').disabled).toBe(false)
    expect(root.querySelector('[role="switch"]').getAttribute('aria-disabled')).not.toBe('true')
    button('Manuell übernehmen').click()
    await nextTick()
    expect(socket.send).toHaveBeenLastCalledWith('{"type":"detach"}')
    expect(root.textContent).toContain('Übernahme angefordert')
    expect(button('Manuell übernehmen').disabled).toBe(true)
    if (viewport.width >= 600) expect(button('Text einfügen').disabled).toBe(true)
    expect(browser.manual).toBe(false)
    socket.message({ type: 'state', browserId: 'runtime-1', viewId, canAttach: true, attached: false, busy: true })
    await nextTick()
    expect(root.textContent).toContain('Agent getrennt. Die laufende Aktion wird noch beendet')
    if (viewport.width >= 600) expect(button('Text einfügen').disabled).toBe(true)
    expect(browser.manual).toBe(false)
    expect(root.querySelector('[role="switch"]').getAttribute('aria-disabled')).toBe('true')
    socket.message({ type: 'state', browserId: 'runtime-1', viewId, canAttach: true, attached: false, busy: false })
    await nextTick()
    if (viewport.width >= 600) expect(button('Text einfügen').disabled).toBe(false)
    expect(browser.manual).toBe(true)
  }
)

it('explicitly copies native clipboard text in both directions without hidden synchronization', async () => {
  Object.assign(browser, { sessionId: 'session-1', integrationId: 'browser-1', browserId: 'runtime-1', viewId, status: 'open', viewStatus: 'open' })
  await mount(BrowserApp)
  const text = 'Grüße 世界 😀\nzweite\tZeile'
  api.get.mockResolvedValueOnce({ data: { text } })
  button('Text kopieren').click()
  await vi.waitFor(() => expect(document.querySelector('textarea')?.value).toBe(text))
  expect(writeText).not.toHaveBeenCalled()
  writeText.mockResolvedValueOnce()
  button('Lokal kopieren').click()
  await vi.waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith(text))
  button('Schließen').click()
  await vi.waitFor(() => expect(document.querySelector('textarea')).toBeNull())
  button('Text einfügen').click()
  await vi.waitFor(() => expect(document.querySelector('textarea')).not.toBeNull())
  expect(readText).not.toHaveBeenCalled()
  expect(api.put).not.toHaveBeenCalled()
  readText.mockResolvedValueOnce(text)
  button('Lokal lesen').click()
  await vi.waitFor(() => expect(document.querySelector('textarea')?.value).toBe(text))
  expect(api.put).not.toHaveBeenCalled()
  api.put.mockResolvedValueOnce({ data: { text } })
  button('Zwischenablage setzen').click()
  await vi.waitFor(() => expect(api.put).toHaveBeenCalledExactlyOnceWith('/browsers/browser-1/clipboard', { text }, transferConfig()))
  expect(writeText).toHaveBeenCalledTimes(1)
  expect(readText).toHaveBeenCalledTimes(1)
})

it('ignores a closed copy dialog response and disables local copy until a successful fresh GET', async () => {
  Object.assign(browser, { sessionId: 'session-1', integrationId: 'browser-1', browserId: 'runtime-1', viewId, status: 'open', viewStatus: 'open' })
  await mount(BrowserApp)
  let finish
  api.get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  button('Text kopieren').click()
  await vi.waitFor(() => expect(button('Lokal kopieren')).toBeDefined())
  expect(button('Lokal kopieren').disabled).toBe(true)
  button('Schließen').click()
  await vi.waitFor(() => expect(document.querySelector('textarea')).toBeNull())
  finish({ data: { text: 'old secret' } })
  await vi.waitFor(() => expect(browser.manual).toBe(true))
  api.get.mockRejectedValueOnce(new Error('API failure'))
  button('Text kopieren').click()
  await vi.waitFor(() => expect(document.body.textContent).toContain('Browsertext konnte nicht geladen werden'))
  expect(document.querySelector('textarea').value).toBe('')
  expect(button('Lokal kopieren').disabled).toBe(true)
  expect(writeText).not.toHaveBeenCalled()
})

it('offers the Browser shortcut in Agent-Coding only for the enabled integration on the selected computer', async () => {
  const integrations = useIntegrationStore()
  const openApp = vi.spyOn(useControlCenterStore(), 'openApp').mockResolvedValue()
  await mount(AgentCodingApp)
  const browserAction = () => [...root.querySelectorAll('.q-item')].find((item) => item.textContent.includes('Browser'))
  expect(browserAction()).toBeDefined()
  browserAction().click()
  expect(openApp).toHaveBeenCalledExactlyOnceWith('browser')
  expect(FakeWebSocket.instances).toHaveLength(0)
  integrations.browser.instances[0].executionAgentId = 'different-agent'
  await nextTick()
  expect(browserAction()).toBeUndefined()
  integrations.browser.instances[0].executionAgentId = 'agent-1'
  integrations.browser.instances[0].enabled = false
  await nextTick()
  expect(browserAction()).toBeUndefined()
  integrations.browser.instances[0].enabled = true
  integrations.catalog[0].availability = 'disabled'
  await nextTick()
  expect(browserAction()).toBeUndefined()
})

it('selects files with Space and enters directories with Enter', async () => {
  Object.assign(browser, { sessionId: 'session-1', integrationId: 'browser-1', browserId: 'runtime-1', viewId, status: 'open', viewStatus: 'open' })
  await mount(BrowserApp)
  api.get.mockResolvedValueOnce({ data: { entries: [
    { kind: 'directory', name: 'Dokumente', path: 'docs' },
    { kind: 'file', name: 'bericht.txt', path: 'bericht.txt', size: 4 }
  ] } })
  await fileAction('Datei hochladen')
  await vi.waitFor(() => expect(document.body.textContent).toContain('bericht.txt'))
  const entries = [...document.querySelectorAll('[aria-label="Dateien und Ordner"] [role="button"]')]
  const directory = entries.find((entry) => entry.textContent.includes('Dokumente'))
  const file = entries.find((entry) => entry.textContent.includes('bericht.txt'))
  expect(document.querySelector('[role="listbox"], [role="option"]')).toBeNull()
  expect(file.tabIndex).toBe(0)
  file.focus()
  file.dispatchEvent(new KeyboardEvent('keyup', { key: ' ', keyCode: 32, bubbles: true, cancelable: true }))
  await nextTick()
  expect(browser.pickerSelected?.path).toBe('bericht.txt')
  expect(file.getAttribute('aria-pressed')).toBe('true')
  api.get.mockResolvedValueOnce({ data: { entries: [] } })
  directory.focus()
  directory.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', keyCode: 13, bubbles: true, cancelable: true }))
  await vi.waitFor(() => expect(browser.pickerDirectories.session).toBe('docs'))
  expect(api.get).toHaveBeenLastCalledWith('/workspace/entries', {
    params: { scope: 'session', session_id: 'session-1', root_identity: 'session-1', path: 'docs' },
    signal: expect.any(AbortSignal), suppressErrorToast: true
  })
})

it.each([true, false])('imports local files through the workspace and uses the typed source, with automatic choose=%s', async (chosen) => {
  Object.assign(browser, { ...{ sessionId: 'session-1', integrationId: 'browser-1', browserId: 'runtime-1', viewId }, status: 'open', viewStatus: 'open' })
  await mount(BrowserApp, { width: 390 })
  const notify = vi.spyOn(app.config.globalProperties.$q, 'notify').mockImplementation(() => {})
  api.get.mockResolvedValueOnce({ data: { entries: [{ kind: 'file', name: 'vorhanden.txt', path: 'vorhanden.txt', size: 4 }] } })
  await fileAction('Datei hochladen')
  await vi.waitFor(() => expect(document.querySelector('input[type="file"]')).not.toBeNull())
  await vi.waitFor(() => expect(document.body.textContent).toContain('vorhanden.txt'))
  expect(document.body.textContent).toContain('Ablage')
  expect(document.body.textContent).toContain('Computer-Dateien')
  const input = document.querySelector('input[type="file"]')
  expect(input.getAttribute('aria-label')).toBe('Lokale Datei auswählen')
  const uploadButton = document.querySelector('[aria-label="Lokale Datei hinzufügen"]')
  uploadButton.click()
  const file = new File(['content'], 'example.txt')
  Object.defineProperty(input, 'files', { configurable: true, value: [file] })
  api.post.mockResolvedValueOnce({ data: { entry: { kind: 'file', name: 'example.txt', path: 'example.txt', size: file.size } } })
  api.post.mockResolvedValueOnce({ data: { uploadId: 'staged-id', chosen } })
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await vi.waitFor(() => expect(api.post).toHaveBeenCalledTimes(2))
  expect(api.post.mock.calls[0][0]).toBe('/workspace/files')
  expect(api.post.mock.calls[0][1].get('file').name).toBe(file.name)
  expect(api.post.mock.calls[0][2].params).toEqual({ parent: '.', scope: 'session', session_id: 'session-1' })
  expect(api.post.mock.calls[1]).toEqual(['/browsers/browser-1/upload-source', {
    source: { scope: 'session', path: 'example.txt' }
  }, transferConfig()])
  expect(api.post.mock.calls.filter(([path, body]) => path === '/browsers/browser-1/upload-source' && body instanceof FormData)).toEqual([])
  if (!chosen) {
    await vi.waitFor(() => expect(button('Datei einsetzen')).toBeDefined())
    expect(document.activeElement).toBe(uploadButton)
    expect(browser.uploadId).toBe('staged-id')
    expect(document.body.textContent).not.toContain('Upload-ID')
    expect(document.body.textContent).toContain('Datei bereitgestellt, aber noch nicht eingesetzt')
    expect(notify).not.toHaveBeenCalled()
    api.post.mockResolvedValueOnce({ data: { ok: true } })
    button('Datei einsetzen').click()
    await vi.waitFor(() => expect(api.post).toHaveBeenCalledTimes(3))
    expect(api.post).toHaveBeenLastCalledWith('/browsers/browser-1/upload/staged-id/choose', null, transferConfig())
  }
  await vi.waitFor(() => expect(notify).toHaveBeenCalledExactlyOnceWith({ type: 'positive', message: 'Datei in das Upload-Feld eingesetzt.' }))
  expect(browser.uploadId).toBeNull()
  await vi.waitFor(() => expect(document.querySelector('input[type="file"]')).toBeNull())
})

it('shows saved and failed download statuses and opens the existing computer files app', async () => {
  Object.assign(browser, { sessionId: 'session-1', integrationId: 'browser-1', browserId: 'runtime-1', viewId, status: 'open', viewStatus: 'open' })
  await mount(BrowserApp)
  api.get.mockResolvedValueOnce({ data: { entries: [] } })
  api.get.mockResolvedValueOnce({ data: { downloads: [
    { id: 'saved', filename: 'bericht.pdf', size: 12, status: 'saved', path: '/home/user/browser/downloads/bericht.pdf' },
    { id: 'permission', filename: 'privat.txt', size: 2, status: 'permission_required' },
    { id: 'failed', filename: 'kaputt.zip', size: 3, status: 'failed' }
  ] } })
  await fileAction('Downloads')
  await vi.waitFor(() => expect(document.body.textContent).toContain('Automatisch gespeichert'))
  expect(document.body.textContent).toContain('/home/user/browser/downloads/bericht.pdf')
  expect(document.body.textContent).toContain('Berechtigung zum Speichern erforderlich')
  expect(document.body.textContent).toContain('Speichern fehlgeschlagen')
  const center = useControlCenterStore()
  const setFileView = vi.spyOn(center, 'setFileView').mockResolvedValue()
  const openApp = vi.spyOn(center, 'openApp').mockResolvedValue()
  button('Downloads öffnen').click()
  await vi.waitFor(() => expect(openApp).toHaveBeenCalledWith('computer-files'))
  expect(setFileView).toHaveBeenCalledWith({
    scope: 'filesystem', selectedPath: '/home/user/browser/downloads', selectedKind: 'directory', viewedFilePath: null,
    executionAgentId: 'agent-1', currentDirectory: '/home/user'
  })
})

it('opens setup from settings without a chat and starts only after confirmation', async () => {
  await mount({ render: () => h(BrowserSetupDialog, { modelValue: true, integrationId: 'browser-1' }) }, { width: 390 })
  await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1))
  const socket = FakeWebSocket.instances[0]
  expect(socket.url).toContain('/browsers/browser-1/setup')
  expect(socket.url).not.toContain('sessionId')
  socket.open()
  socket.message({ type: 'state', state: 'missing' })
  await nextTick()
  expect(socket.send.mock.calls).toEqual([['{"type":"status"}']])
  confirm.mockResolvedValueOnce(false)
  button('Einrichtung starten').click()
  await nextTick()
  expect(socket.send.mock.calls).toEqual([['{"type":"status"}']])
  confirm.mockResolvedValueOnce(true)
  button('Einrichtung starten').click()
  await vi.waitFor(() => expect(socket.send).toHaveBeenLastCalledWith('{"type":"start"}'))
  socket.message({ type: 'state', state: 'installing' })
  await nextTick()
  expect(button('Einrichtung starten').disabled).toBe(true)
  app.unmount()
  app = null
  expect(socket.close).toHaveBeenCalledOnce()
  expect(socket.send.mock.calls.filter(([raw]) => JSON.parse(raw).type === 'close')).toHaveLength(0)
})
