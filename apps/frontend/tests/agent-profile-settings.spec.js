// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QAvatar, QBanner, QBtn, QBtnToggle, QCard, QCardSection, QIcon, QInnerLoading, QItem, QItemLabel, QItemSection, QList, QToggle, QTooltip } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { delete: vi.fn(), get: vi.fn() } }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: async () => true }) }))

import Page from '../src/pages/settings/AgentProfileSettingsPage.vue'
import { api } from 'src/boot/axios'
import { useAuthStore } from '../src/stores/auth'

let app, root

afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.clearAllMocks()
})

it('shows disabled personal profiles without error or empty states', async () => {
  api.get.mockResolvedValue({
    data: {
      defaultAgentProfileId: 'general',
      personalAgentsEnabled: false,
      effectiveAgents: [{ id: 'general', name: 'Allgemein', scope: 'instance' }]
    }
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/agents', name: 'settings-agents', component: Page },
      { path: '/agents/new', name: 'settings-agent-create', component: { render: () => null } },
      { path: '/agents/:agentId', name: 'settings-agent-edit', component: { render: () => null } }
    ]
  })
  await router.push('/agents?scope=personal')
  root = document.createElement('div')
  document.body.append(root)
  const pinia = createPinia()
  app = createApp({ render: () => h(Page) })
  app.use(pinia).use(router).use(Quasar, {
    components: {
      QAvatar, QBanner, QBtn, QBtnToggle, QCard, QCardSection, QIcon,
      QInnerLoading, QItem, QItemLabel, QItemSection, QList, QToggle, QTooltip
    }
  })
  useAuthStore(pinia).isAdmin = true
  app.mount(root)

  await vi.waitFor(() => expect(root.textContent).toContain(
    'Persönliche Agenten wurden für diese Instanz deaktiviert.'
  ))
  expect(root.textContent).not.toContain('Die Agentenprofile konnten nicht geladen werden.')
  expect(root.textContent).not.toContain('Noch keine Agentenprofile')
  expect(root.textContent).not.toContain('Neues Profil erstellen')
})
