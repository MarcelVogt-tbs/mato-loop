// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import MainLayout from '../src/layouts/MainLayout.vue'

const { chat, projects, pending, quasar, terminal, workspace } = vi.hoisted(() => {
  const sessions = [
    { id: 'standalone', title: 'Standalone', projectId: null },
    { id: 'project-session', title: 'Project session', projectId: 'a' }
  ]
  const sessionLists = Object.fromEntries([null, 'a', 'b'].map((id) => [id ?? 'standalone', {
    loaded: true, items: sessions.filter((session) => session.projectId === id)
  }]))
  return {
    terminal: { syncContext: vi.fn(), release: vi.fn(), close: vi.fn() },
    workspace: { hasDirtyEditors: false },
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
vi.mock('vue-router', () => ({
  useRoute: () => ({ name: 'chat', query: {} }), useRouter: () => ({ push: vi.fn() }),
  onBeforeRouteLeave: vi.fn(), onBeforeRouteUpdate: vi.fn()
}))
vi.mock('stores/chat', () => ({ useChatStore: () => chat }))
vi.mock('stores/projects', () => ({ useProjectStore: () => projects }))
vi.mock('stores/approvals', () => ({ useApprovalStore: () => pending }))
vi.mock('stores/questions', () => ({ useQuestionStore: () => pending }))
vi.mock('stores/agent-identity', () => ({ useAgentIdentityStore: () => ({ fetchIdentity: vi.fn().mockResolvedValue() }) }))
vi.mock('stores/in-app-notifications', () => ({ useInAppNotificationStore: () => ({
  unreadCount: 0, fetchLatest: vi.fn().mockResolvedValue(), reconcilePush: vi.fn().mockResolvedValue()
}) }))
vi.mock('stores/control-center', () => ({ useControlCenterStore: () => ({ sessionId: null, open: false, tabs: [], confirmedState: { tabs: [] } }) }))
vi.mock('stores/terminal', () => ({ useTerminalStore: () => terminal }))
vi.mock('stores/browser', () => ({ useBrowserStore: () => ({ syncContext: vi.fn(), release: vi.fn() }) }))
vi.mock('stores/integrations', () => ({ useIntegrationStore: () => ({
  fetchCatalog: vi.fn().mockResolvedValue(), fetchBrowser: vi.fn().mockResolvedValue()
}) }))
vi.mock('stores/auth', () => ({ useAuthStore: () => ({ username: 'Tester' }) }))
vi.mock('stores/automations', () => ({ useAutomationStore: () => ({}) }))
vi.mock('stores/execution-agents', () => ({ useExecutionAgentStore: () => ({}) }))
vi.mock('stores/workspace', () => ({ useWorkspaceStore: () => workspace }))
vi.mock('stores/search', () => ({ useSearchStore: () => ({ openDialog: vi.fn() }) }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: vi.fn() }) }))
vi.mock('src/utils/notifications', () => ({ notifyError: vi.fn() }))
vi.mock('components/AccountDialog.vue', () => ({ default: { render: () => null } }))
vi.mock('components/SearchDialog.vue', () => ({ default: { render: () => null } }))
vi.mock('components/ControlCenterPanel.vue', () => ({ default: { render: () => null } }))

let app, root
beforeEach(async () => {
  vi.clearAllMocks()
  workspace.hasDirtyEditors = false
  vi.useFakeTimers()
  root = document.createElement('div')
  app = createApp(MainLayout)
  app.config.globalProperties.$q = quasar
  const container = { render() { return h('div', this.$slots.default?.()) } }
  for (const name of ['QLayout', 'QDrawer', 'QToolbar', 'QSpace', 'QSeparator', 'QScrollArea', 'QItem', 'QItemSection', 'QItemLabel', 'QIcon', 'QList', 'QAvatar', 'QBadge', 'QCard', 'QCardSection', 'QCardActions', 'QBanner', 'QPageContainer', 'RouterLink']) {
    app.component(name, container)
  }
  for (const name of ['QTooltip', 'QMenu', 'QInput', 'QSkeleton', 'QSpinnerDots', 'RouterView']) app.component(name, { render: () => null })
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

it.each([false, true])('beforeunload only warns about dirty editors (%s), leaving a cancelled exit connected', (dirty) => {
  workspace.hasDirtyEditors = dirty
  const event = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(dirty)
  expect(terminal.release).not.toHaveBeenCalled()
  expect(terminal.close).not.toHaveBeenCalled()
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
