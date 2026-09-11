// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QAvatar, QBanner, QBtn, QBtnToggle, QCard, QCardSection, QCheckbox, QIcon, QInnerLoading, QSelect, QSeparator, QToggle } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), put: vi.fn(), patch: vi.fn() } }))

import DefaultModelSettingsPage from '../src/pages/settings/DefaultModelSettingsPage.vue'
import { api } from 'src/boot/axios'

let app, root

afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.clearAllMocks()
})

it('shows the inherited effective title model in personal settings', async () => {
  api.get.mockImplementation(async (url) => {
    if (url === '/providers/personal-policy') {
      return { data: { personalProvidersEnabled: true } }
    }
    if (url.endsWith('/codex/status')) return { data: { authenticated: false } }
    return { data: {
      providers: [],
      models: [],
      availableProviders: [{ id: 'instance-provider', name: 'AI-PC', enabled: true }],
      availableModels: [{
        id: 'instance-title', providerId: 'instance-provider', modelId: 'gemma',
        name: 'Gemma', capabilities: ['chat'], enabled: true
      }],
      titleGenerationModelId: null,
      effectiveTitleGenerationModelId: 'instance-title'
    } }
  })
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(DefaultModelSettingsPage) })
  app.use(createPinia()).use(Quasar, { components: {
    QAvatar, QBanner, QBtn, QBtnToggle, QCard, QCardSection, QCheckbox,
    QIcon, QInnerLoading, QSelect, QSeparator, QToggle
  } })
  app.mount(root)

  await vi.waitFor(() => {
    expect(root.textContent).toContain('Instanzvorgabe: Gemma · AI-PC')
  })
})
