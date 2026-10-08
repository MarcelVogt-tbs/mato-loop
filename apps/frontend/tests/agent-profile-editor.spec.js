// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar } from 'quasar'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: async () => true }) }))

import Editor from '../src/features/agent-profiles/AgentProfileEditorPage.vue'
import { api } from 'src/boot/axios'
import { useAuthStore } from '../src/stores/auth'

let app, root
const builtinId = '00000000-0000-4000-8000-000000000001'
async function mountEditor(path = '/agents/agent', extraAgents = [], personalAgentsEnabled = true, isAdmin = true) {
  const builtin = { id: builtinId, scope: 'instance', protected: true, accessEditable: true, name: 'Allgemein', description: 'Standard', instructions: 'Follow the request.', maxToolCalls: 25, todoEnabled: false, delegationEnabled: false, sessionWritePaths: null, overrides: {}, accessBaseline: { instructions: 'Follow the request.', titleVisionFallbackEnabled: true } }
  let agents = [builtin, { id: 'agent', scope: 'personal', protected: false, name: 'Recherche', instructions: 'Check sources.', maxToolCalls: null, todoEnabled: true, delegationEnabled: true, sessionWritePaths: [], baseProfileId: builtinId, overrides: { maxToolCalls: null, todoEnabled: true, delegationEnabled: true, sessionWritePaths: [], privatePermissions: { policies: { 'shell.write': 'ask' } } } }, ...extraAgents]
  api.get.mockImplementation(async () => ({ data: { defaultAgentProfileId: builtinId, baseProfiles: agents.filter((item) => item.protected), agents, effectiveAgents: agents, personalAgentsEnabled } }))
  api.put.mockImplementation(async (url, payload) => {
    const id = url.split('/')[2]
    agents = agents.map((agent) => agent.id === id ? { ...agent, ...(url.endsWith('/access') ? payload : payload.overrides), overrides: url.endsWith('/access') ? payload : payload.overrides } : agent)
    return { data: {} }
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/agents/new', name: 'settings-agent-create', component: Editor },
    { path: '/agents/:agentId', name: 'settings-agent-edit', component: Editor },
    { path: '/agents', name: 'settings-agents', component: { render: () => null } },
    { path: '/admin/agents/new', name: 'admin-agent-create', component: Editor },
    { path: '/admin/agents/:agentId', name: 'admin-agent-edit', component: Editor },
    { path: '/admin/agents', name: 'admin-agents', component: { render: () => null } },
    { path: '/tools', name: 'settings-tools', component: { render: () => null } },
    { path: '/admin/tools', name: 'admin-tools', component: { render: () => null } },
    { path: '/integrations', name: 'settings-integrations', component: { render: () => null } },
    { path: '/admin/integrations', name: 'admin-integrations', component: { render: () => null } },
    { path: '/personas', name: 'settings-persona', component: { render: () => null } },
    { path: '/personalization', name: 'settings-personalization', component: { render: () => null } },
    { path: '/admin/personas', name: 'admin-personas', component: { render: () => null } }
  ] })
  await router.push(path)
  root = document.createElement('div')
  document.body.append(root)
  let page
  app = createApp({ render: () => h(RouterView, null, { default: ({ Component }) => h(Component, { ref: (value) => { page = value?.$?.setupState } }) }) })
  const pinia = createPinia()
  app.use(pinia).use(router).use(Quasar, quasarOptions)
  useAuthStore(pinia).isAdmin = isAdmin
  app.config.globalProperties.$q.notify = vi.fn()
  app.mount(root)
  await vi.waitFor(() => expect(page?.savedPayload || page?.blocked).toBeTruthy())
  await nextTick()
  return page
}
afterEach(() => { app?.unmount(); root?.remove(); vi.clearAllMocks() })

it('starts a sparse profile from Allgemein and retains explicit content when changing its base', async () => {
  const page = await mountEditor('/agents/new?copyFrom=agent', [{ id: 'plan', scope: 'instance', protected: true, name: 'Plan', instructions: 'Create a plan.', maxToolCalls: null, delegationEnabled: true, repositoryInstructionsEnabled: true }])
  expect(page.requestPayload()).toEqual({ name: '', description: '', baseProfileId: builtinId, overrides: {} })
  page.setOwnership('instructions', 'own')
  page.form.instructions = 'Eigene Aufgabe'
  page.form.baseProfileId = 'plan'
  page.applyInheritedValues()
  expect(page.form).toMatchObject({ instructions: 'Eigene Aufgabe', maxToolCalls: null, delegationEnabled: true, repositoryInstructionsEnabled: true })
})

it('uses the administration route for global creation', async () => {
  const page = await mountEditor('/admin/agents/new')
  expect(page.scope).toBe('instance')
  expect(api.get).toHaveBeenCalledWith('/agent-profiles', { params: { scope: 'instance' } })
  expect(root.querySelector('.q-checkbox')?.textContent).not.toContain('Global')
  page.section = 'access'
  await nextTick()
  expect([...root.querySelectorAll('button')].filter((button) => button.textContent.includes('öffnen')).every((button) => button.disabled)).toBe(true)
})

it.each([
  ['/agents/agent', [], 'agent', '/personalization?tab=assistant&profileId=agent', ''],
  [`/admin/agents/${builtinId}`, [], builtinId, `/admin/personas?profileId=${builtinId}`, '/admin'],
  ['/admin/agents/custom', [{ id: 'custom', scope: 'instance', name: 'Globaler Agent', protected: false, baseProfileId: builtinId, overrides: {} }], builtinId, '/admin/personas?profileId=custom', '/admin']
])('bundles contextual persona and access links for %s', async (path, extra, accessId, personaLink, prefix) => {
  await mountEditor(path, extra)
  const tab = [...root.querySelectorAll('[role="tab"]')].find((item) => item.textContent.includes('Weitere Einstellungen'))
  tab.click()
  await nextTick()
  expect([...root.querySelectorAll('.q-tab-panel a')].map((link) => link.getAttribute('href'))).toEqual([
    personaLink, `${prefix}/tools?profileId=${accessId}`, `${prefix}/integrations?profileId=${accessId}`
  ])
})

it('blocks a regular users personal editor when personal profiles are disabled', async () => {
  const page = await mountEditor('/agents/agent', [], false, false)
  expect(page.blocked).toBe(true)
  expect(root.querySelector('form')).toBeNull()
})

it('round-trips profile content without sending permissions through the profile API', async () => {
  const page = await mountEditor()
  page.setModelValue('todoEnabled', false)
  page.setModelValue('repositoryInstructionsEnabled', true)
  await page.saveAgent()
  expect(api.put).toHaveBeenCalledWith('/agent-profiles/agent', expect.objectContaining({ overrides: {
    maxToolCalls: null, todoEnabled: false, delegationEnabled: true, repositoryInstructionsEnabled: true, sessionWritePaths: []
  } }), { params: { scope: 'personal' } })
})

it('inherits, overrides, and clears runtime instructions', async () => {
  const page = await mountEditor()
  page.assignForm({ ...page.editingAgent, runtimeInstructions: 'Inspect only.', overrides: { runtimeInstructions: 'Inspect only.' } })
  expect(page.requestPayload().overrides.runtimeInstructions).toBe('Inspect only.')
  page.form.runtimeInstructions = ''
  expect(page.requestPayload().overrides.runtimeInstructions).toBe('')
  page.setOwnership('runtimeInstructions', 'inherit')
  expect(page.requestPayload().overrides).not.toHaveProperty('runtimeInstructions')
})

it('validates finite limits and preserves content after a failed save', async () => {
  const page = await mountEditor()
  page.form.maxToolCalls = 0
  page.section = 'access'
  await page.saveAgent()
  expect(api.put).not.toHaveBeenCalled()
  page.form.maxToolCalls = 7
  api.put.mockRejectedValueOnce(new Error('request failed'))
  await page.saveAgent()
  expect(page.form.maxToolCalls).toBe(7)
  expect(page.dirty).toBe(true)
})

it('preserves all session write path meanings and validates paths', async () => {
  const page = await mountEditor()
  page.form.sessionWriteMode = 'unrestricted'
  expect(page.requestPayload().overrides.sessionWritePaths).toBeNull()
  page.form.sessionWriteMode = 'denied'
  expect(page.requestPayload().overrides.sessionWritePaths).toEqual([])
  page.form.sessionWriteMode = 'paths'
  page.form.sessionWritePathsText = 'plan.md\nausgabe/bericht.md'
  expect(page.requestPayload().overrides.sessionWritePaths).toEqual(['plan.md', 'ausgabe/bericht.md'])
  page.form.sessionWritePathsText = '../secret'
  expect(page.sessionWritePathsRule()).not.toBe(true)
})

it('keeps built-in workflows read-only without model configuration', async () => {
  const page = await mountEditor(`/admin/agents/${builtinId}`)
  expect(page.isProtected).toBe(true)
  await page.saveAgent()
  expect(api.put).not.toHaveBeenCalled()
  expect([...root.querySelectorAll('[role="tab"]')].map((item) => item.textContent)).not.toContain('Modelle')
})
