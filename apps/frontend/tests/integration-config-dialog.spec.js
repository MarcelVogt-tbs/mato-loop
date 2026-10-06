// @vitest-environment jsdom
import { createApp, h, nextTick, ref } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import {
  Quasar, QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection,
  QCheckbox, QDialog, QForm, QIcon, QInput, QItem, QItemSection,
  QSelect, QSeparator, QSpace, QToggle
} from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn() } }))
vi.mock('src/features/browser/BrowserSetupDialog.vue', () => ({ default: { render: () => null } }))

import Dialog from '../src/features/integrations/IntegrationConfigDialog.vue'
import { useAuthStore } from '../src/stores/auth'
import { useExecutionAgentStore } from '../src/stores/execution-agents'
import { useIntegrationStore } from '../src/stores/integrations'

let app, root
afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.restoreAllMocks()
})

it('keeps the ownership selected by the administration or settings page', async () => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const pinia = createPinia()
  useAuthStore(pinia).isAdmin = true
  const integrations = useIntegrationStore(pinia)
  integrations.selectScope('personal')
  integrations.catalog = [
    { adapterType: 'caldav', executionLocation: 'backend', availability: 'full' },
    { adapterType: 'browser', executionLocation: 'agent', availability: 'full' },
    { adapterType: 'sonobin', executionLocation: 'backend', availability: 'full' }
  ]
  vi.spyOn(useExecutionAgentStore(pinia), 'fetchAll').mockResolvedValue()
  let page
  const open = ref(false)
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(Dialog, { modelValue: open.value, ref: (value) => { page = value?.$?.setupState } }) })
  app.use(pinia).use(Quasar, { components: {
    QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox,
    QDialog, QForm, QIcon, QInput, QItem, QItemSection, QSelect,
    QSeparator, QSpace, QToggle
  } })
  app.mount(root)
  open.value = true

  await vi.waitFor(() => expect(document.querySelector('.integration-dialog')).not.toBeNull())
  expect(document.querySelector('.integration-dialog .q-checkbox')).toBeNull()
  expect(integrations.scope).toBe('personal')
  page.form.integration_type = 'browser'
  page.selectIntegrationType('browser')
  await nextTick()
  expect(document.querySelector('.integration-dialog .q-checkbox')).toBeNull()
  page.form.integration_type = 'music'
  expect(page.urlRule('https://sonobin.example.com')).toBe(true)
})
