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
import { useAuthStore } from '../src/stores/auth'

let app, root
const first = '00000000-0000-4000-8000-000000000010'
const second = '00000000-0000-4000-8000-000000000011'
const builtinId = '00000000-0000-4000-8000-000000000001'

async function mountEditor(path = '/agents/agent?scope=personal', extraAgents = []) {
  const builtin = {
    id: builtinId, scope: 'instance', protected: true, name: 'Allgemein', description: 'Standard',
    personaId: null, instructions: 'Follow the request.', answerDisplay: 'compact', maxToolCalls: 25,
    todoEnabled: false, integrationsEnabledByDefault: true, integrationAvailability: {},
    privatePermissions: { policies: {}, integrationPolicies: {}, resourcePolicies: {} },
    publicPermissions: { policies: {}, integrationPolicies: {}, resourcePolicies: {} },
    delegationEnabled: false, sessionWritePaths: null, baseProfileId: null
  }
  let agent = {
    id: 'agent', scope: 'personal', protected: false, name: 'Recherche', instructions: 'Check sources.',
    answerDisplay: 'timeline',
    maxToolCalls: null, todoEnabled: true, integrationsEnabledByDefault: false,
    integrationAvailability: { [first]: true, [second]: false },
    privatePermissions: { policies: {}, integrationPolicies: { [first]: { 'calendar.read': 'ask' } }, resourcePolicies: {} },
    publicPermissions: { policies: {}, integrationPolicies: {}, resourcePolicies: {} },
    delegationEnabled: true, sessionWritePaths: [], baseProfileId: builtinId,
    overrides: {
      answerDisplay: 'timeline', maxToolCalls: null, todoEnabled: true,
      integrationsEnabledByDefault: false, integrationAvailability: { [first]: true, [second]: false },
      privatePermissions: { policies: {}, integrationPolicies: { [first]: { 'calendar.read': 'ask' } }, resourcePolicies: {} },
      delegationEnabled: true, sessionWritePaths: []
    }
  }
  api.get.mockImplementation(async () => ({ data: {
    defaultAgentProfileId: builtinId,
    effectiveAgents: [builtin, agent, ...extraAgents], personalAgentsEnabled: true,
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
  const pinia = createPinia()
  app.use(pinia).use(router).use(Quasar, { components: { QAvatar, QBadge, QBanner, QBtn, QBtnToggle, QCard, QCardSection, QChip, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QList, QSelect, QSeparator, QTab, QTabPanel, QTabPanels, QTabs, QToggle, QToolbar, QToolbarTitle } })
  useAuthStore(pinia).isAdmin = true
  app.config.globalProperties.$q.notify = vi.fn()
  app.mount(root)
  await vi.waitFor(() => expect(page?.savedPayload).toBeTruthy())
  await nextTick()
  return page
}

it('starts a profile from Allgemein without copying fields or honoring copyFrom', async () => {
  const plan = {
    id: 'plan', scope: 'instance', protected: true, name: 'Planen', description: '',
    instructions: 'Create a plan.', answerDisplay: 'timeline', maxToolCalls: null,
    todoEnabled: true, integrationsEnabledByDefault: false, integrationAvailability: {},
    privatePermissions: { policies: {}, integrationPolicies: {}, resourcePolicies: {} },
    publicPermissions: { policies: {}, integrationPolicies: {}, resourcePolicies: {} },
    delegationEnabled: true, sessionWritePaths: [], baseProfileId: null
  }
  const integration = {
    ...plan, id: 'support', scope: 'integration', name: 'Support-Assistent'
  }
  const page = await mountEditor('/agents/new?scope=personal&copyFrom=agent', [plan, integration])

  expect(page.editingAgent).toBeNull()
  expect(page.form).toMatchObject({
    name: '', description: '', baseProfileId: builtinId,
    instructions: 'Follow the request.', maxToolCalls: 25, integrationsEnabledByDefault: true
  })
  expect(page.ownership.instructions).toBe(false)
  expect(page.requestPayload()).toEqual({ name: '', description: '', baseProfileId: builtinId, overrides: {} })
  expect(page.baseProfileOptions.map((option) => option.value)).toEqual([builtinId, 'plan'])
  expect(root.textContent).toContain('Nicht angepasste Einstellungen werden vom Basisprofil übernommen.')

  page.setOwnership('instructions', 'own')
  page.form.instructions = 'Eigene Aufgabe'
  page.form.baseProfileId = 'plan'
  page.applyInheritedValues()
  expect(page.form).toMatchObject({ instructions: 'Eigene Aufgabe', maxToolCalls: null, delegationEnabled: true })
  expect(page.requestPayload().overrides).toEqual({ instructions: 'Eigene Aufgabe' })
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
  expect(root.querySelector('[role="group"][aria-label="Antwortdarstellung übernehmen oder anpassen"]')).not.toBeNull()
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
    baseProfileId: builtinId,
    overrides: expect.objectContaining({
      maxToolCalls: null, todoEnabled: true, integrationsEnabledByDefault: true,
      answerDisplay: 'compact',
      integrationAvailability: { [first]: false },
      privatePermissions: JSON.parse(policies)
    })
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
  page.agents.saving = true
  await page.saveAgent()
  expect(api.put).not.toHaveBeenCalled()
  page.agents.saving = false
  api.put.mockRejectedValueOnce(new Error('request failed'))
  await page.saveAgent()
  expect(page.form.maxToolCalls).toBe(7)
  expect(page.dirty).toBe(true)
  await page.saveAgent()
  expect(api.put).toHaveBeenLastCalledWith('/agent-profiles/agent', expect.objectContaining({
    overrides: expect.objectContaining({ maxToolCalls: 7 })
  }), expect.anything())
  expect(page.dirty).toBe(false)
})

it('limits profile policy options to the action metadata', async () => {
  const page = await mountEditor()

  expect(page.policyOptions('ask', ['disabled', 'ask']).map(({ value }) => value)).toEqual([
    'inherit', 'disabled', 'ask'
  ])
})

it('removes overrides on inherit and preserves all session write path meanings', async () => {
  const page = await mountEditor()

  page.setOwnership('delegationEnabled', 'inherit')
  expect(page.form.delegationEnabled).toBe(false)
  expect(page.requestPayload().overrides).not.toHaveProperty('delegationEnabled')

  page.setOwnership('sessionWritePaths', 'own')
  page.form.sessionWriteMode = 'unrestricted'
  expect(page.requestPayload().overrides.sessionWritePaths).toBeNull()
  page.form.sessionWriteMode = 'denied'
  expect(page.requestPayload().overrides.sessionWritePaths).toEqual([])
  page.form.sessionWriteMode = 'paths'
  page.form.sessionWritePathsText = 'plan.md\nausgabe/bericht.md'
  expect(page.sessionWritePathsRule()).toBe(true)
  expect(page.requestPayload().overrides.sessionWritePaths).toEqual(['plan.md', 'ausgabe/bericht.md'])
  page.form.sessionWritePathsText = '../secret'
  expect(page.sessionWritePathsRule()).not.toBe(true)
})

it('keeps integration default and selection overrides independent', async () => {
  const sparse = {
    id: 'sparse', scope: 'personal', protected: false, name: 'Sparse', description: '',
    baseProfileId: builtinId, overrides: { integrationAvailability: { [first]: false } },
    personaId: null, instructions: 'Follow the request.', answerDisplay: 'compact', maxToolCalls: 25,
    todoEnabled: false, integrationsEnabledByDefault: true, integrationAvailability: { [first]: false },
    privatePermissions: { policies: {}, integrationPolicies: {}, resourcePolicies: {} },
    publicPermissions: { policies: {}, integrationPolicies: {}, resourcePolicies: {} },
    delegationEnabled: false, sessionWritePaths: null
  }
  const page = await mountEditor('/agents/sparse?scope=personal', [sparse])

  expect(page.ownership.integrationsEnabledByDefault).toBe(false)
  expect(page.ownership.integrationAvailability).toBe(true)
  expect(page.requestPayload().overrides).toEqual({ integrationAvailability: { [first]: false } })
})

it('shows shipped profiles read-only without a save action', async () => {
  const page = await mountEditor(`/agents/${builtinId}?scope=instance`)

  expect(page.isProtected).toBe(true)
  expect(page.form.baseProfileId).toBeNull()
  expect(page.dirty).toBe(false)
  expect(root.textContent).toContain('schreibgeschützt')
  expect(root.textContent).not.toContain('Agentenprofil speichern')
  expect(root.querySelector('form').hasAttribute('inert')).toBe(false)
  expect([...root.querySelectorAll('[role="tab"]')].every((tab) => !tab.hasAttribute('disabled'))).toBe(true)
})
