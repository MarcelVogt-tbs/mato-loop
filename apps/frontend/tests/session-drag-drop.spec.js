// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import MainLayout from '../src/layouts/MainLayout.vue'
import { useChatStore } from 'stores/chat'
import { useBrowserStore } from 'stores/browser'
import { useControlCenterStore } from 'stores/control-center'
import { useIntegrationStore } from 'stores/integrations'
import { useRoute } from 'vue-router'

const { chat, confirm, projects, pending, quasar, routeUpdate, terminal, workspace } = vi.hoisted(() => {
  const sessions = [
    { id: 'standalone', title: 'Standalone', projectId: null },
    { id: 'project-session', title: 'Project session', projectId: 'a' }
  ]
  const sessionLists = Object.fromEntries([null, 'a', 'b'].map((id) => [id ?? 'standalone', {
    loaded: true, items: sessions.filter((session) => session.projectId === id)
  }]))
  return {
    terminal: { syncContext: vi.fn(), release: vi.fn(), close: vi.fn() },
    workspace: {
      hasDirtyEditors: false, sessionId: null,
      fileTabDirty: vi.fn(() => false), discardFileTab: vi.fn()
    },
    confirm: vi.fn(),
    routeUpdate: { callback: null },
    chat: {
      cachedSessions: sessions, sessionLists,
      sessionList: (id) => sessionLists[id ?? 'standalone'],
      ensureSessionList: (id) => sessionLists[id ?? 'standalone'],
      setSessionScope: vi.fn().mockResolvedValue(),
      moveSession: vi.fn().mockResolvedValue()
    },
    projects: { loaded: true, items: [{ id: 'a', name: 'Alpha' }, { id: 'b', name: 'Beta' }], fetchAll: vi.fn().mockResolvedValue() },
    pending: { pending: [], forSession: () => [], fetchPending: vi.fn().mockResolvedValue() },
    quasar: { dark: { isActive: false }, screen: { lt: { sm: false, md: false, lg: false } }, platform: { is: {} }, notify: vi.fn() }
  }
})
vi.mock('quasar', () => ({ useQuasar: () => quasar }))
vi.mock('vue-router', async () => {
  const { reactive } = await import('vue')
  const route = reactive({ name: 'chat', query: {} })
  const router = { push: vi.fn(async (to) => {
    const next = { name: to.name, query: to.query || {} }
    if (await routeUpdate.callback?.(next, { name: route.name, query: route.query }) === false) return false
    route.name = next.name
    route.query = next.query
    return true
  }) }
  return {
    useRoute: () => route,
    useRouter: () => router,
    onBeforeRouteLeave: vi.fn(),
    onBeforeRouteUpdate: (callback) => { routeUpdate.callback = callback }
  }
})
vi.mock('stores/chat', async () => {
  const { reactive } = await import('vue')
  const store = reactive(chat)
  return { useChatStore: () => store }
})
vi.mock('stores/projects', () => ({ useProjectStore: () => projects }))
vi.mock('stores/approvals', () => ({ useApprovalStore: () => pending }))
vi.mock('stores/questions', () => ({ useQuestionStore: () => pending }))
vi.mock('stores/agent-identity', () => ({ useAgentIdentityStore: () => ({ fetchIdentity: vi.fn().mockResolvedValue() }) }))
vi.mock('stores/in-app-notifications', () => ({ useInAppNotificationStore: () => ({
  unreadCount: 0, fetchLatest: vi.fn().mockResolvedValue(), reconcilePush: vi.fn().mockResolvedValue()
}) }))
vi.mock('stores/control-center', async () => {
  const { reactive } = await import('vue')
  const store = reactive({
    sessionId: null, ready: true, open: false, tabs: [], fileTabs: [], confirmedState: { tabs: [] },
    removeFileTabs: vi.fn().mockResolvedValue()
  })
  return { useControlCenterStore: () => store }
})
vi.mock('stores/terminal', () => ({ useTerminalStore: () => terminal }))
vi.mock('stores/browser', async () => {
  const { reactive } = await import('vue')
  const store = reactive({ sessionId: null, agentId: null, integrationId: null,
    syncContext: vi.fn(), release: vi.fn(), setDisplayedSession: vi.fn(), reconcileControlRequest: vi.fn() })
  return { useBrowserStore: () => store }
})
vi.mock('stores/integrations', async () => {
  const { reactive } = await import('vue')
  const store = reactive({ fetchCatalog: vi.fn().mockResolvedValue(), fetchBrowser: vi.fn().mockResolvedValue(),
    adapterAvailable: vi.fn(() => true), browser: { instances: [] }, browserLoaded: true, catalogLoaded: true, catalogLoadFailed: false })
  return { useIntegrationStore: () => store }
})
vi.mock('stores/auth', () => ({ useAuthStore: () => ({ username: 'Tester' }) }))
vi.mock('stores/automations', () => ({ useAutomationStore: () => ({}) }))
vi.mock('stores/execution-agents', () => ({ useExecutionAgentStore: () => ({ byId: (id) => id === 'agent-1' ? { connected: true, operations: [] } : null }) }))
vi.mock('stores/workspace', () => ({ useWorkspaceStore: () => workspace }))
vi.mock('stores/search', () => ({ useSearchStore: () => ({ openDialog: vi.fn() }) }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm }) }))
vi.mock('src/utils/notifications', () => ({ notifyError: vi.fn() }))
vi.mock('components/AccountDialog.vue', () => ({ default: { render: () => null } }))
vi.mock('components/SearchDialog.vue', () => ({ default: { render: () => null } }))
vi.mock('components/ControlCenterPanel.vue', () => ({ default: { render: () => null } }))

let app, root
beforeEach(async () => {
  vi.clearAllMocks()
  useChatStore().current = null
  useRoute().name = 'chat'
  useRoute().query = {}
  Object.assign(useBrowserStore(), { sessionId: null, agentId: null, integrationId: null })
  Object.assign(useControlCenterStore(), { sessionId: null, ready: true, tabs: [], fileTabs: [], confirmedState: { tabs: [] } })
  useIntegrationStore().browser.instances = []
  Object.assign(useIntegrationStore(), { browserLoaded: true, catalogLoaded: true, catalogLoadFailed: false })
  useIntegrationStore().adapterAvailable.mockReturnValue(true)
  workspace.hasDirtyEditors = false
  workspace.sessionId = null
  workspace.fileTabDirty.mockReturnValue(false)
  vi.useFakeTimers()
  root = document.createElement('div')
  app = createApp(MainLayout)
  app.config.globalProperties.$q = quasar
  const container = { render() { return h('div', this.$slots.default?.()) } }
  for (const name of ['QLayout', 'QDrawer', 'QToolbar', 'QSpace', 'QSeparator', 'QScrollArea', 'QItem', 'QItemSection', 'QItemLabel', 'QIcon', 'QList', 'QAvatar', 'QBadge', 'QCard', 'QCardSection', 'QCardActions', 'QBanner', 'QPageContainer', 'RouterLink']) {
    app.component(name, container)
  }
  for (const name of ['QTooltip', 'QInput', 'QSkeleton', 'QSpinnerDots', 'RouterView']) app.component(name, { render: () => null })
  app.component('QMenu', {
    props: ['modelValue'], emits: ['update:modelValue'],
    render() {
      return h('span', { class: 'menu-stub' }, [
        h('button', { class: 'menu-stub__show', onClick: () => this.$emit('update:modelValue', true) }),
        h('button', { class: 'menu-stub__hide', onClick: () => this.$emit('update:modelValue', false) }),
        this.modelValue ? this.$slots.default?.() : null
      ])
    }
  })
  app.component('QExpansionItem', {
    props: ['modelValue'], emits: ['update:modelValue', 'show'],
    render() {
      return h('div', [h('button', { onClick: () => {
        this.$emit('update:modelValue', !this.modelValue)
        if (!this.modelValue) this.$emit('show')
      } }, this.$slots.header?.()), this.modelValue ? this.$slots.default?.() : null])
    }
  })
  app.component('QDialog', {
    props: ['modelValue'], render() { return this.modelValue ? h('div', { role: 'dialog' }, this.$slots.default?.()) : null }
  })
  app.component('QSelect', {
    props: ['modelValue', 'options'], render() {
      return h('select', { value: this.modelValue ?? '' }, this.options.map(({ label, value }) => h('option', { value: value ?? '' }, label)))
    }
  })
  app.component('QForm', { render() { return h('form', this.$slots.default?.()) } })
  app.component('QBtn', {
    props: ['label', 'disable'], render() { return h('button', { disabled: this.disable }, [this.label, this.$slots.default?.()]) }
  })
  for (const name of ['ripple', 'close-popup', 'confirm']) app.directive(name, {})
  app.mount(root)
  root.querySelector('.project-list-item > button').click()
  await nextTick()
})
afterEach(() => { app?.unmount(); vi.useRealTimers() })

it('keeps project and session actions visible while their shared menu is open', async () => {
  const projectAction = root.querySelector('[aria-label="Aktionen für Alpha"]')
  projectAction.querySelector('.menu-stub__show').click()
  await nextTick()
  expect(projectAction.closest('.project-list-item').classList.contains('project-list-item--menu-open')).toBe(true)
  projectAction.querySelector('.menu-stub__hide').click()
  await nextTick()
  expect(projectAction.closest('.project-list-item').classList.contains('project-list-item--menu-open')).toBe(false)

  const sessionAction = root.querySelector('[aria-label="Aktionen für Project session"]')
  sessionAction.querySelector('.menu-stub__show').click()
  await nextTick()
  expect(sessionAction.closest('.session-list-item').classList.contains('session-list-item--menu-open')).toBe(true)
})

it.each([false, true])('beforeunload only warns about dirty editors (%s), leaving a cancelled exit connected', (dirty) => {
  workspace.hasDirtyEditors = dirty
  const event = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(dirty)
  expect(terminal.release).not.toHaveBeenCalled()
  expect(terminal.close).not.toHaveBeenCalled()
})

it('evaluates the retained browser agent and tab, not the destination or the current=null load gap', async () => {
  const browser = useBrowserStore()
  const center = useControlCenterStore()
  useIntegrationStore().browser.instances = [{ id: 'browser-1', executionAgentId: 'agent-1', enabled: true }]
  Object.assign(browser, { sessionId: 'session-1', agentId: 'agent-1', integrationId: 'browser-1' })
  useChatStore().current = { id: 'session-2', preferredExecutionAgentId: 'offline-agent' }
  useRoute().query = { session: 'session-2' }
  center.sessionId = 'session-2'
  await nextTick()
  expect(browser.syncContext).toHaveBeenLastCalledWith({ sessionId: 'session-2', agentId: 'offline-agent', available: true, tabPresent: true })
  useChatStore().current = null
  await nextTick()
  expect(browser.syncContext).toHaveBeenLastCalledWith({ sessionId: undefined, agentId: undefined, available: true, tabPresent: true })
  useChatStore().current = { id: 'session-1', preferredExecutionAgentId: 'agent-1' }
  useRoute().query = { session: 'session-1' }
  center.sessionId = 'session-1'
  center.ready = false
  await nextTick()
  expect(browser.syncContext).toHaveBeenLastCalledWith({ sessionId: 'session-1', agentId: 'agent-1', available: true, tabPresent: true })
  center.ready = true
  await nextTick()
  expect(browser.syncContext).toHaveBeenLastCalledWith({ sessionId: 'session-1', agentId: 'agent-1', available: true, tabPresent: false })
  useIntegrationStore().browser.instances[0].enabled = false
  await nextTick()
  expect(browser.syncContext).toHaveBeenLastCalledWith({ sessionId: 'session-1', agentId: 'agent-1', available: false, tabPresent: false })
})

it('retains browser ownership through settings cache invalidation and failed catalog refresh, but accepts authoritative revocation', async () => {
  const browser = useBrowserStore()
  const integrations = useIntegrationStore()
  const instance = { id: 'browser-1', executionAgentId: 'agent-1', enabled: true }
  Object.assign(browser, { sessionId: 'session-1', agentId: 'agent-1', integrationId: 'browser-1' })
  integrations.browser.instances = [instance]
  useChatStore().current = { id: 'session-1', preferredExecutionAgentId: 'agent-1' }
  useRoute().query = { session: 'session-1' }
  useRoute().name = 'settings-integrations'
  // selectScope clears cached data before the independent personal browser fetch completes.
  integrations.browser.instances = []
  integrations.browserLoaded = false
  await nextTick()
  expect(browser.syncContext).toHaveBeenLastCalledWith({ sessionId: 'session-1', agentId: 'agent-1', available: true, tabPresent: true })
  integrations.browser.instances = [instance]
  integrations.browserLoaded = true
  integrations.catalogLoadFailed = true
  integrations.adapterAvailable.mockReturnValue(false)
  await nextTick()
  expect(browser.syncContext).toHaveBeenLastCalledWith({ sessionId: 'session-1', agentId: 'agent-1', available: true, tabPresent: true })
  integrations.catalogLoadFailed = false
  integrations.adapterAvailable.mockReturnValue(true)
  useRoute().name = 'chat'
  await nextTick()
  expect(browser.syncContext).toHaveBeenLastCalledWith({ sessionId: 'session-1', agentId: 'agent-1', available: true, tabPresent: true })
  integrations.browser.instances = [{ ...instance, enabled: false }]
  await nextTick()
  expect(browser.syncContext).toHaveBeenLastCalledWith({ sessionId: 'session-1', agentId: 'agent-1', available: false, tabPresent: true })
  integrations.browser.instances = []
  await nextTick()
  expect(browser.syncContext).toHaveBeenLastCalledWith({ sessionId: 'session-1', agentId: 'agent-1', available: false, tabPresent: true })
  integrations.browser.instances = [instance]
  integrations.adapterAvailable.mockReturnValue(false)
  await nextTick()
  expect(browser.syncContext).toHaveBeenLastCalledWith({ sessionId: 'session-1', agentId: 'agent-1', available: false, tabPresent: true })
})

function source(id) {
  const scope = id === 'standalone' ? '#standalone-session-list' : '.project-list-item'
  return root.querySelector(`${scope} .session-list-item__content`)
}
function target(id) {
  return id === null ? root.querySelector('[aria-labelledby="sessions-heading"]')
    : root.querySelectorAll('.project-list-item')[id === 'a' ? 0 : 1]
}
async function drag(element, type, dataTransfer, relatedTarget = null) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  Object.assign(event, { dataTransfer, relatedTarget })
  element.dispatchEvent(event)
  await nextTick()
  return event
}

it.each([
  ['standalone', 'a'], ['project-session', 'b'], ['project-session', null]
])('drops %s onto %s into the move dialog, moving only on confirmation', async (id, destination) => {
  const transfer = { setData: vi.fn() }
  expect(source(id).getAttribute('draggable')).toBe('true')
  await drag(source(id), 'dragstart', transfer)
  expect(transfer.effectAllowed).toBe('move')
  expect(transfer.setData).toHaveBeenCalledWith('application/x-matoloop-session', id)
  expect((await drag(target(destination), 'dragover', transfer)).defaultPrevented).toBe(true)
  expect(transfer.dropEffect).toBe('move')
  expect(target(destination).classList.contains('app-surface-tonal')).toBe(true)
  expect((await drag(target(destination), 'drop', transfer)).defaultPrevented).toBe(true)
  expect(target(destination).classList.contains('app-surface-tonal')).toBe(false)
  const dialog = root.querySelector('[role="dialog"]')
  expect(dialog.textContent).toContain('Session verschieben')
  expect(dialog.querySelector('select').value).toBe(destination ?? '')
  expect(chat.moveSession).not.toHaveBeenCalled()
  dialog.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await nextTick()
  expect(chat.moveSession).toHaveBeenCalledExactlyOnceWith(id, destination)
})

it('moves the current session to another project without guarding its unchanged file root', async () => {
  useChatStore().current = { id: 'project-session', projectId: 'b' }
  useRoute().query = { session: 'project-session', project: 'a' }
  workspace.hasDirtyEditors = true
  const transfer = { setData: vi.fn() }
  await drag(source('project-session'), 'dragstart', transfer)
  await drag(target('b'), 'drop', transfer)

  root.querySelector('[role="dialog"] form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))

  await vi.waitFor(() => expect(useRoute().query).toEqual({ session: 'project-session', project: 'b' }))
  expect(confirm).not.toHaveBeenCalled()
  expect(workspace.hasDirtyEditors).toBe(true)
})

it.each([['standalone', null], ['project-session', 'a'], [null, 'b']])(
  'ignores same-destination or external drags: %s onto %s', async (id, destination) => {
    const transfer = { setData: vi.fn(), types: ['application/x-matoloop-session'], getData: () => 'standalone' }
    if (id) await drag(source(id), 'dragstart', transfer)
    expect((await drag(target(destination), 'dragover', transfer)).defaultPrevented).toBe(false)
    expect(target(destination).classList.contains('app-surface-tonal')).toBe(false)
    expect((await drag(target(destination), 'drop', transfer)).defaultPrevented).toBe(false)
    expect(root.querySelector('[role="dialog"]')).toBeNull()
    expect(chat.moveSession).not.toHaveBeenCalled()
  }
)

it('keeps highlighting within a target, clears it on leave, and clears drag state on dragend', async () => {
  const transfer = { setData: vi.fn() }
  await drag(source('project-session'), 'dragstart', transfer)
  const destination = target(null)
  await drag(destination, 'dragover', transfer)
  await drag(destination, 'dragleave', transfer, destination.firstElementChild)
  expect(destination.classList.contains('app-surface-tonal')).toBe(true)
  await drag(destination, 'dragleave', transfer)
  expect(destination.classList.contains('app-surface-tonal')).toBe(false)
  await drag(destination, 'dragover', transfer)
  expect(destination.classList.contains('app-surface-tonal')).toBe(true)
  await drag(source('project-session'), 'dragend', transfer)
  expect(destination.classList.contains('app-surface-tonal')).toBe(false)
  expect((await drag(destination, 'dragover', transfer)).defaultPrevented).toBe(false)
  expect((await drag(destination, 'drop', transfer)).defaultPrevented).toBe(false)
  expect(root.querySelector('[role="dialog"]')).toBeNull()
  expect(chat.moveSession).not.toHaveBeenCalled()
})
