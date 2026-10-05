// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QAvatar, QBadge, QBanner, QBtn, QCheckbox, QCard, QCardSection, QIcon, QInnerLoading, QItem, QItemLabel, QItemSection, QList, QSelect, QTab, QTabs, QToggle, QTooltip } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { delete: vi.fn(), get: vi.fn(), patch: vi.fn() } }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: async () => true }) }))

import Page from '../src/features/agent-profiles/AgentProfileSettingsPage.vue'
import { api } from 'src/boot/axios'
import { useAuthStore } from '../src/stores/auth'

let app, root

afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

it('shows disabled personal profiles without error or empty states', async () => {
  api.get.mockResolvedValue({
    data: {
      defaultAgentProfileId: 'general',
      personalAgentsEnabled: false,
      effectiveAgents: [{ id: 'general', name: 'Allgemein', scope: 'instance' }],
      agents: [{ id: 'mine', name: 'Mein Agent', scope: 'personal', effective: false }]
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
      QAvatar, QBadge, QBanner, QBtn, QCheckbox, QCard, QCardSection, QIcon,
      QInnerLoading, QItem, QItemLabel, QItemSection, QList, QSelect, QTab, QTabs, QToggle, QTooltip
    }
  })
  useAuthStore(pinia).isAdmin = false
  app.mount(root)

  await vi.waitFor(() => expect(root.textContent).toContain(
    'Persönliche Agenten wurden für diese Instanz deaktiviert.'
  ))
  expect(root.textContent).not.toContain('Die Agentenprofile konnten nicht geladen werden.')
  expect(root.textContent).not.toContain('Noch keine Agentenprofile')
  expect(root.textContent).not.toContain('Neues Profil erstellen')
  expect(root.querySelector('.q-checkbox')).toBeNull()
})

it.each([
  { width: 390, dark: false, admin: false }, { width: 390, dark: true, admin: false },
  { width: 1440, dark: false, admin: false }, { width: 1440, dark: true, admin: false },
  { width: 390, dark: false, admin: true }
])('shows scoped profile management at $width px (dark=$dark, admin=$admin)', async ({ width, dark, admin }) => {
  vi.stubGlobal('innerWidth', width)
  const data = {
    defaultAgentProfileId: 'general',
    personalAgentsEnabled: true,
    effectiveAgents: [
      { id: 'general', name: 'Allgemein', scope: 'instance', protected: true },
      { id: 'mine', name: 'Mein Agent', scope: 'personal' }
    ],
    agents: [
      { id: 'general', name: 'Allgemein', scope: 'instance', protected: true },
      { id: 'team', name: 'Team Agent', scope: 'instance', enabled: false },
      { id: 'mine', name: 'Mein Agent', scope: 'personal', enabled: true }
    ]
  }
  api.get.mockResolvedValue({ data })
  api.patch.mockImplementation(async (_url, { enabled }) => {
    data.personalAgentsEnabled = enabled
    return { data: { personalAgentsEnabled: enabled } }
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/agents', name: 'settings-agents', component: Page },
      { path: '/agents/new', name: 'settings-agent-create', component: { render: () => null } },
      { path: '/agents/:agentId', name: 'settings-agent-edit', component: { render: () => null } },
      { path: '/admin/agents/new', name: 'admin-agent-create', component: { render: () => null } },
      { path: '/admin/agents/:agentId', name: 'admin-agent-edit', component: { render: () => null } }
    ]
  })
  await router.push('/agents')
  root = document.createElement('div')
  document.body.append(root)
  const pinia = createPinia()
  app = createApp({ render: () => h(Page, { admin }) })
  app.use(pinia).use(router).use(Quasar, { components: {
    QAvatar, QBadge, QBanner, QBtn, QCheckbox, QCard, QCardSection, QIcon,
    QInnerLoading, QItem, QItemLabel, QItemSection, QList, QSelect, QTab, QTabs, QToggle, QTooltip
  } })
  useAuthStore(pinia).isAdmin = admin
  app.config.globalProperties.$q.dark.set(dark)
  app.mount(root)

  await vi.waitFor(() => expect(root.textContent).toContain(admin ? 'Persönliche Agentenprofile erlauben' : 'Mein Agent'))
  if (admin) {
    expect(root.querySelector('[role="tab"][aria-selected="true"]').textContent).toContain('Einstellungen')
    expect(root.textContent).not.toContain('Neues Profil erstellen')
    expect(root.textContent).not.toContain('Team Agent')
    await vi.waitFor(() => expect(root.querySelector('[aria-label="Persönliche Agentenprofile erlauben"]').getAttribute('aria-disabled')).not.toBe('true'))
    root.querySelector('[aria-label="Persönliche Agentenprofile erlauben"]').click()
    await vi.waitFor(() => expect(api.patch).toHaveBeenCalledWith('/agent-profiles/personal-enabled', { enabled: false }))
    root.querySelector('[role="tab"][aria-selected="false"]').click()
    await vi.waitFor(() => expect(root.textContent).toContain('Allgemein'))
  } else {
    expect(root.textContent).not.toContain('Allgemein')
    expect(root.textContent).toContain('Persönlich')
    expect(root.textContent).not.toContain('Global')
    expect(root.textContent).not.toContain('Team Agent')
  }
  expect(root.querySelector('.q-checkbox')).toBeNull()
})
