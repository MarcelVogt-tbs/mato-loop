// @vitest-environment jsdom
import { createApp, h, nextTick, reactive } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QBanner, QBtn, QIcon, QItem, QItemLabel, QItemSection, QList, QMenu, QSeparator, QSpinner, QTab, QTabs, QTooltip, ClosePopup } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
const mocks = vi.hoisted(() => ({ store: null, confirm: vi.fn(async () => true) }))
vi.mock('stores/automations', () => ({ useAutomationStore: () => mocks.store }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: mocks.confirm }) }))
import List from '../src/features/automations/AutomationSettingsPage.vue'

let app, root
afterEach(() => { app?.unmount(); root?.remove(); vi.restoreAllMocks() })

it('opens a compact row, preserves the archive filter, and stops hidden list polling including legacy links', async () => {
  const store = reactive({
    items: [{ id: 'one', name: 'Bericht', taskExcerpt: 'Ein kurzer Auftrag', revision: 1, enabled: true, scheduleCount: 0, effectiveState: { enabled: false, activeRunStatus: null } }],
    loading: false, fetchAutomations: vi.fn(async () => {}), runNow: vi.fn(async () => ({ id: 'run' }))
  })
  mocks.store = store
  const timers = new Map()
  let counter = 0
  vi.spyOn(window, 'setInterval').mockImplementation((callback) => { timers.set(++counter, callback); return counter })
  vi.spyOn(window, 'clearInterval').mockImplementation((id) => timers.delete(id))
  const warnings = vi.spyOn(console, 'warn').mockImplementation(() => {})
  const router = createRouter({ history: createMemoryHistory(), routes: [{
    path: '/automations', name: 'settings-automations', component: List,
    children: [
      { path: 'new', name: 'settings-automation-create', component: { render: () => 'new' } },
      { path: ':automationId', name: 'settings-automation-detail', component: { render: () => 'detail' } }
    ]
  }] })
  await router.push('/automations')
  root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(RouterView) })
  app.use(router).use(Quasar, { components: { QBanner, QBtn, QIcon, QItem, QItemLabel, QItemSection, QList, QMenu, QSeparator, QSpinner, QTab, QTabs, QTooltip }, directives: { ClosePopup } })
  app.mount(root)
  await nextTick()
  expect(root.querySelectorAll('.q-card')).toHaveLength(0)
  expect(root.textContent).toContain('Nur manuell')
  expect(timers.size).toBe(1)
  root.querySelector('[role="link"]').click()
  await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('settings-automation-detail'))
  expect(timers.size).toBe(0)
  await router.push('/automations')
  await nextTick()
  const archive = [...root.querySelectorAll('[role="tab"]')].find((el) => el.textContent === 'Archiv')
  archive.click()
  await nextTick()
  expect(store.fetchAutomations).toHaveBeenLastCalledWith({ archived: true, suppressErrorToast: false })
  await router.push('/automations?automation=one&run=legacy-run')
  await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('settings-automation-detail'))
  expect(router.currentRoute.value.query).toEqual({ run: 'legacy-run' })
  expect(timers.size).toBe(0)
  await router.push('/automations')
  expect(store.fetchAutomations).toHaveBeenLastCalledWith({ archived: true, suppressErrorToast: false })
  expect(warnings).not.toHaveBeenCalled()
})
