// @vitest-environment jsdom
import { createApp, h, reactive } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import {
  Quasar, QAvatar, QBadge, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox,
  QChip, ClosePopup, QDialog, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel,
  QItemSection, QLinearProgress, QList, QMenu, QSelect, QSeparator, QSpace, QTab, QTabs, QToggle, QTooltip
} from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn() } }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: async () => true }) }))

import Page from '../src/features/providers/ProviderSettingsPage.vue'
import { api } from 'src/boot/axios'
import { useAuthStore } from '../src/stores/auth'
import { useProviderStore } from '../src/stores/providers'

let app, root
afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.clearAllMocks()
})

it('keeps global and personal lists and creation scopes separate when switching pages', async () => {
  api.get.mockImplementation((url, config) => {
    if (url === '/providers/personal-policy') return Promise.resolve({ data: { personalProvidersEnabled: false } })
    if (url === '/providers/codex/status') return Promise.resolve({ data: { authenticated: false } })
    if (url === '/providers' && config?.params?.scope === 'instance') return Promise.resolve({ data: {
      providers: [{ id: 'shared', name: 'Team', adapterType: 'openai-compatible', enabled: true, config: { baseUrl: 'https://team.example' } }],
      models: []
    } })
    if (url === '/providers' && config?.params?.scope === 'personal') return Promise.resolve({ data: {
      providers: [{ id: 'mine', name: 'Privat', adapterType: 'openai-compatible', enabled: true, config: { baseUrl: 'https://mine.example' } }],
      models: []
    } })
    throw new Error(`Unexpected request: ${url}`)
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { render: () => null } },
    { path: '/settings/personalization', name: 'settings-personalization', component: { render: () => null } }
  ] })
  await router.push('/')
  root = document.createElement('div')
  document.body.append(root)
  const pinia = createPinia()
  const props = reactive({ admin: true })
  app = createApp({ render: () => h(Page, props) })
  app.use(pinia).use(router).use(Quasar, { directives: { ClosePopup }, components: {
    QAvatar, QBadge, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox,
    QChip, QDialog, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel,
    QItemSection, QLinearProgress, QList, QMenu, QSelect, QSeparator, QSpace, QTab, QTabs, QToggle, QTooltip
  } })
  useAuthStore(pinia).isAdmin = true
  app.mount(root)
  const providers = useProviderStore(pinia)
  for (const admin of [true, false]) {
    props.admin = admin
    if (admin) {
      const tabs = [...root.querySelectorAll('[role="tab"]')]
      expect(tabs[0].textContent).toContain('Einstellungen')
      await vi.waitFor(() => expect(root.querySelector('[aria-label="Persönliche Provider erlauben"]')).not.toBeNull())
      expect(root.querySelector('.provider-card-list')).toBeNull()
      expect(root.textContent).not.toContain('Provider hinzufügen')
      tabs.find((tab) => tab.textContent.includes('Verwaltung')).click()
      await vi.waitFor(() => expect(root.textContent).toContain('Team'))
    } else {
      await vi.waitFor(() => expect(root.querySelector('[role="tab"]')).toBeNull())
    }
    await vi.waitFor(() => expect(root.textContent).toContain(admin ? 'Team' : 'Privat'))
    expect(root.textContent).not.toContain(admin ? 'Privat' : 'Team')
    providers.selectScope(admin ? 'personal' : 'instance')
    const create = [...root.querySelectorAll('button')].find((button) => button.textContent.includes('Provider hinzufügen'))
    create.click()
    await vi.waitFor(() => expect(document.querySelector('.provider-dialog')).not.toBeNull())
    expect(providers.scope).toBe(admin ? 'instance' : 'personal')
    expect([...document.querySelectorAll('.provider-dialog .q-checkbox')].some((item) => item.textContent.includes('Global'))).toBe(false)
    document.querySelector('.provider-dialog button[aria-label="Dialog schließen"]').click()
    await vi.waitFor(() => expect(document.querySelector('.provider-dialog')).toBeNull())
  }
})
