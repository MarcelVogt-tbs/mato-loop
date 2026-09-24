// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import {
  Quasar, QAvatar, QBadge, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox,
  QChip, ClosePopup, QDialog, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel,
  QItemSection, QLinearProgress, QList, QMenu, QSelect, QSeparator, QSpace, QToggle, QTooltip
} from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn() } }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: async () => true }) }))

import Page from '../src/features/providers/ProviderSettingsPage.vue'
import { api } from 'src/boot/axios'
import { useAuthStore } from '../src/stores/auth'

let app, root
afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.clearAllMocks()
})

it('lists shared and owned providers for a regular user without exposing shared configuration', async () => {
  api.get.mockImplementation((url, config) => {
    if (url === '/providers/personal-policy') return Promise.resolve({ data: { personalProvidersEnabled: true } })
    if (url === '/providers' && !config?.params?.scope) return Promise.resolve({ data: {
      providers: [{ id: 'shared', name: 'Team', adapterType: 'openai-compatible', enabled: true, config: {} }],
      models: []
    } })
    if (url === '/providers' && config?.params?.scope === 'personal') return Promise.resolve({ data: {
      providers: [{ id: 'mine', name: 'Privat', adapterType: 'openai-compatible', enabled: true, config: { baseUrl: 'https://mine.example' } }],
      models: []
    } })
    throw new Error(`Unexpected request: ${url}`)
  })
  root = document.createElement('div')
  document.body.append(root)
  const pinia = createPinia()
  app = createApp({ render: () => h(Page) })
  app.use(pinia).use(Quasar, { directives: { ClosePopup }, components: {
    QAvatar, QBadge, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox,
    QChip, QDialog, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel,
    QItemSection, QLinearProgress, QList, QMenu, QSelect, QSeparator, QSpace, QToggle, QTooltip
  } })
  useAuthStore(pinia).isAdmin = false
  app.mount(root)

  await vi.waitFor(() => expect(root.textContent).toContain('Globale Verbindung'))
  expect(root.textContent).toContain('Team')
  expect(root.textContent).toContain('Privat')
  expect(root.textContent).toContain('Global')
  expect(root.querySelector('.q-checkbox')).toBeNull()
  expect(api.get).not.toHaveBeenCalledWith('/providers', { params: { scope: 'instance' } })
})
