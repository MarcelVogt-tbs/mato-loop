// @vitest-environment jsdom
import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import {
  Quasar, QBanner, QBtn, QIcon, QItem, QItemLabel, QItemSection, QList, QRadio,
  QScrollArea, QSeparator, QSpinner, QToolbar, QToolbarTitle, Ripple
} from 'quasar'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
const { api } = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn() }
}))
vi.mock('src/boot/axios', () => ({ api }))

import TbsDeskApp from '../src/features/tbs-desk/TbsDeskApp.vue'
import { useChatStore } from '../src/features/chat/store'
import { useControlCenterStore } from '../src/stores/control-center'
import { useIntegrationStore } from '../src/stores/integrations'

const state = {
  available: true,
  integrationId: 'tbs-1',
  candidates: [{ id: 'customer-1', erpId: 'ERP-7', displayName: 'Muster GmbH', activeCall: true, label: 'Muster GmbH | ERP-7' }],
  selectedCustomer: null,
  tickets: [],
  questionId: 'question-1'
}
const components = {
  QBanner, QBtn, QIcon, QItem, QItemLabel, QItemSection, QList, QRadio,
  QScrollArea, QSeparator, QSpinner, QToolbar, QToolbarTitle
}
let app, root, controlCenter

async function flush() {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountApp({ width = 1440, dark = false, accountEnabled = true } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const chat = useChatStore()
  chat.current = { id: 'session-1' }
  const integrations = useIntegrationStore()
  integrations.tbsDesk = { configured: true, accounts: [{ id: 'tbs-1', enabled: accountEnabled }] }
  controlCenter = useControlCenterStore()
  controlCenter.syncSession({
    id: 'session-1',
    controlCenter: { tabs: [{ appId: 'tbs-desk' }], activeAppId: 'tbs-desk', open: true }
  })
  root = document.createElement('div')
  document.body.append(root)
  app = createApp(TbsDeskApp)
  app.use(pinia).use(Quasar, { components, directives: { Ripple }, config: { dark } })
  app.config.globalProperties.$q.dark.set(dark)
  app.config.globalProperties.$q.screen.lt.md = width < 1024
  app.mount(root)
  await flush()
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockResolvedValue({ data: state })
  api.post.mockResolvedValue({ data: { status: 'answered' } })
})

afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.useRealTimers()
})

describe('tbs.Desk app', () => {
  it.each([false, true])('keeps a persisted tab visible when the account is unavailable (dark=%s)', async (dark) => {
    await mountApp({ dark, accountEnabled: false })

    expect(root.textContent).toContain('tbs.Desk nicht verfügbar')
    expect(api.get).not.toHaveBeenCalled()
  })

  it('submits the exact backend candidate label through the question store', async () => {
    await mountApp()
    root.querySelector('.q-radio').click()
    await nextTick()
    const submit = [...root.querySelectorAll('button')].find((button) => button.textContent.includes('Kunden übernehmen'))
    submit.click()
    await flush()

    expect(api.post).toHaveBeenCalledWith('/questions/question-1/answers', {
      answers: { tbs_customer: ['Muster GmbH | ERP-7'] }
    })
    expect(api.get).toHaveBeenCalledWith('/tbs-desk/sessions/session-1')
  })

  it('polls every second only while the TBS tab is active with pending work', async () => {
    vi.useFakeTimers()
    await mountApp()
    expect(api.get).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1000)
    await flush()
    expect(api.get).toHaveBeenCalledTimes(2)

    controlCenter.activeAppId = 'session'
    await nextTick()
    await vi.advanceTimersByTimeAsync(2000)
    expect(api.get).toHaveBeenCalledTimes(2)
  })

  it('opens a ticket detail and returns to the list on mobile', async () => {
    const mobileState = {
      ...state,
      questionId: null,
      candidates: [],
      selectedCustomer: { id: 'customer-1', erpId: 'ERP-7', displayName: 'Muster GmbH' },
      tickets: [{ id: 'ticket-1', header: 'Drucker defekt', created: '2026-09-15T10:00:00Z', state: 'Offen' }]
    }
    api.get.mockImplementation((url) => Promise.resolve({
      data: url.endsWith('/tickets/ticket-1')
        ? { ...mobileState.tickets[0], entries: [{ id: 'entry-1', text: 'Papierstau' }] }
        : mobileState
    }))
    await mountApp({ width: 390 })

    const ticket = [...root.querySelectorAll('.q-item')].find((item) => item.textContent.includes('Drucker defekt'))
    ticket.click()
    await flush()
    expect(root.textContent).toContain('Papierstau')

    root.querySelector('[aria-label="Zurück zur Ticketliste"]').click()
    await nextTick()
    expect(root.querySelector('[aria-label="Zurück zur Ticketliste"]')).toBeNull()
  })
})
