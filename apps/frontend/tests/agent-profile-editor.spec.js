// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QAvatar, QBadge, QBanner, QBtn, QBtnToggle, QCard, QCardSection, QChip, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QList, QSelect, QSeparator, QTab, QTabPanel, QTabPanels, QTabs, QToggle, QToolbar, QToolbarTitle } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: async () => true }) }))

import Editor from '../src/pages/settings/AgentProfileEditorPage.vue'
import { api } from 'src/boot/axios'

let app, root
const first = '00000000-0000-4000-8000-000000000010'
const second = '00000000-0000-4000-8000-000000000011'

async function mountEditor(path = '/agents/agent?scope=personal', extraAgents = []) {
  let agent = {
    id: 'agent', scope: 'personal', name: 'Recherche', instructions: 'Check sources.',
    answerDisplay: 'timeline',
    maxToolCalls: null, todoEnabled: true, integrationsEnabledByDefault: false,
    integrationAvailability: { [first]: true, [second]: false },
    privatePermissions: { policies: {}, integrationPolicies: { [first]: { 'calendar.read': 'ask' } }, resourcePolicies: {} },
    publicPermissions: { policies: {}, integrationPolicies: {}, resourcePolicies: {} }
  }
  api.get.mockImplementation(async () => ({ data: {
    effectiveAgents: [agent, ...extraAgents], personalAgentsEnabled: true,
    integrations: [
      { id: first, name: 'Kalender', enabled: true, available: true, supportsPolicies: false },
      { id: second, name: 'SearXNG', enabled: true, available: true, supportsPolicies: false }
    ]
  } }))
  api.put.mockImplementation(async (_url, payload) => { agent = { ...agent, ...payload }; return { data: {} } })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/agents/:agentId', name: 'settings-agent-edit', component: Editor },
    { path: '/agents/new', name: 'settings-agent-create', component: Editor },
    { path: '/agents', name: 'settings-agents', component: { render: () => null } },
    { path: '/personas', name: 'settings-personalization', component: { render: () => null } }
  ] })
  await router.push(path)
  root = document.createElement('div')
  document.body.append(root)
  let page
  app = createApp({ render: () => h(RouterView, null, {
    default: ({ Component }) => h(Component, { ref: (value) => { page = value?.$?.setupState } })
  }) })
  app.use(createPinia()).use(router).use(Quasar, { components: { QAvatar, QBadge, QBanner, QBtn, QBtnToggle, QCard, QCardSection, QChip, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QList, QSelect, QSeparator, QTab, QTabPanel, QTabPanels, QTabs, QToggle, QToolbar, QToolbarTitle } })
  app.config.globalProperties.$q.notify = vi.fn()
  app.mount(root)
  await vi.waitFor(() => expect(page?.savedPayload).toBeTruthy())
  await nextTick()
  return page
}

it('starts a personal profile as an independent copy of an instance profile', async () => {
  const template = {
    id: 'instance-template', scope: 'instance', name: 'Quellenprüfung', description: 'Prüft Quellen',
    personaId: 'instance-persona', instructions: 'Arbeite sorgfältig.', maxToolCalls: 9,
    integrationsEnabledByDefault: false, integrationAvailability: { [first]: true },
    privatePermissions: { policies: { 'network.search': 'ask' }, integrationPolicies: {}, resourcePolicies: {} },
    publicPermissions: { policies: {}, integrationPolicies: {}, resourcePolicies: {} }
  }
  const page = await mountEditor('/agents/new?scope=personal&copyFrom=instance-template', [template])

  expect(page.editingAgent).toBeNull()
  expect(page.form).toMatchObject({
    name: 'Kopie von Quellenprüfung', description: 'Prüft Quellen', personaId: 'instance-persona',
    instructions: 'Arbeite sorgfältig.', maxToolCalls: 9, integrationsEnabledByDefault: false
  })
  expect(page.dirty).toBe(true)
  page.form.privatePermissions.policies['network.search'] = 'auto'
  expect(template.privatePermissions.policies['network.search']).toBe('ask')
})

afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.clearAllMocks()
})

it('round-trips unlimited and sparse false overrides without conflating selection and policies', async () => {
  const page = await mountEditor()
  expect(page.form.maxToolCalls).toBeNull()
  expect(page.form.todoEnabled).toBe(true)
  expect(page.form.answerDisplay).toBe('timeline')
  expect(root.textContent).toContain('Zeigt alle Arbeitsschritte direkt')
  expect(page.form.integrationsEnabledByDefault).toBe(false)
  expect(root.textContent).toContain('Modell- und Dienstkosten')
  const policies = JSON.stringify(page.form.privatePermissions)
  page.section = 'integrations'
  await nextTick()
  expect(root.textContent).toContain('SearXNG')
  expect(root.textContent).toContain('Kalender')
  page.form.integrationsEnabledByDefault = true
  page.form.answerDisplay = 'compact'
  expect(page.form.integrationAvailability).toEqual({ [first]: true, [second]: false })
  page.setIntegrationAvailability(first, false)
  expect(page.integrationAvailable({ id: first, enabled: true })).toBe(false)
  expect(page.effectiveIntegrationPolicy('private', first, 'calendar.read')).toBe('disabled')
  expect(JSON.stringify(page.form.privatePermissions)).toBe(policies)
  page.setIntegrationAvailability(second, 'inherit')
  await page.saveAgent()
  expect(api.put).toHaveBeenCalledWith('/agent-profiles/agent', expect.objectContaining({
    maxToolCalls: null, todoEnabled: true, integrationsEnabledByDefault: true,
    answerDisplay: 'compact',
    integrationAvailability: { [first]: false },
    privatePermissions: JSON.parse(policies)
  }), { params: { scope: 'personal' } })
  expect(page.dirty).toBe(false)
})

it('validates finite limits even when another tab is open and preserves edits after a failed save', async () => {
  const page = await mountEditor()
  page.form.maxToolCalls = 0
  page.section = 'integrations'
  await page.saveAgent()
  expect(page.section).toBe('profile')
  expect(api.put).not.toHaveBeenCalled()
  page.form.maxToolCalls = 7
  api.put.mockRejectedValueOnce(new Error('request failed'))
  await page.saveAgent()
  expect(page.form.maxToolCalls).toBe(7)
  expect(page.dirty).toBe(true)
  await page.saveAgent()
  expect(api.put).toHaveBeenLastCalledWith('/agent-profiles/agent', expect.objectContaining({ maxToolCalls: 7 }), expect.anything())
  expect(page.dirty).toBe(false)
})
