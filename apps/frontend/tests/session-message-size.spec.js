// @vitest-environment jsdom
import { createApp, h, nextTick, ref } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QSelect, QIcon, QItem, QItemLabel, QItemSection } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn() } }))

import SessionSettingsFields from '../src/features/chat/components/SessionSettingsFields.vue'
import { useProviderStore } from '../src/stores/providers'
import { useExecutionAgentStore } from '../src/stores/execution-agents'
import { useAuthStore } from '../src/stores/auth'
import { useAgentProfileStore } from '../src/stores/agent-profiles'
import { useSessionTemplateStore } from '../src/features/settings/session-templates-store'
import { api } from 'src/boot/axios'
import { quasarOptions } from './quasar-options'

let app, root
afterEach(() => { app?.unmount(); root?.remove(); vi.restoreAllMocks(); vi.clearAllMocks() })

it('allows all templates in an existing worktree session', async () => {
  const pinia = createPinia()
  useSessionTemplateStore(pinia).templates = [
    { id: 'general', name: 'Allgemein', worktreesEnabled: false },
    { id: 'coding', name: 'Coding', worktreesEnabled: true }
  ]
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(SessionSettingsFields, {
    session: { id: 'session', workspaceMode: 'worktree', sessionProfile: { templateId: 'coding' } }, fields: ['template']
  }) }).use(pinia).use(Quasar, quasarOptions)
  app.mount(root)
  root.querySelector('[role="combobox"]').click()
  await vi.waitFor(() => expect([...document.querySelectorAll('[role="option"]')].map(item => [
    item.querySelector('.q-item__label').textContent, item.getAttribute('aria-disabled') === 'true'
  ])).toEqual([['Allgemein', false], ['Coding', false]]))
})

it('selects Interactive and warns when its required computer is missing', async () => {
  const pinia = createPinia()
  const id = '00000000-0000-4000-8000-000000000006'
  useAgentProfileStore(pinia).agents = [{ id, name: 'Interactive', enabled: true }]
  const session = ref({ agentProfileId: null, sessionProfile: { agentIds: [id] } })
  const update = vi.fn()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(SessionSettingsFields, {
    session: session.value,
    fields: ['agent'], showWarning: true, onUpdate: (patch) => {
      update(patch)
      session.value.agentProfileId = patch.agent_profile_id
    }
  }) }).use(pinia).use(Quasar, quasarOptions)
  app.mount(root)
  root.querySelector('[role="combobox"]').click()
  const option = await vi.waitUntil(() => [...document.querySelectorAll('[role="option"]')].find(item => item.textContent.includes('Interactive')))
  option.click()
  await nextTick()
  expect([update.mock.calls[0]?.[0], root.textContent.includes('Plan, Build und Interactive benötigen einen Computer und ein Arbeitsverzeichnis.')]).toEqual([
    { agent_profile_id: id }, true
  ])
})

it.each([true, false])('edits session trust only with model distinction enabled (%s)', async (enabled) => {
  const pinia = createPinia()
  useAuthStore(pinia).modelTrustEnabled = enabled
  const update = vi.fn()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(SessionSettingsFields, {
    session: { id: 'session', trustLevel: 'public' }, fields: ['trust'], onUpdate: update
  }) }).use(pinia).use(Quasar, quasarOptions)
  app.mount(root)
  if (!enabled) {
    expect(root.textContent).not.toContain('Vertraulichkeit')
    return
  }
  root.querySelector('.q-field__native').click()
  const option = await vi.waitUntil(() => [...document.querySelectorAll('[role="option"]')].find(item => item.textContent.includes('Privat')))
  option.click()
  expect(update).toHaveBeenCalledWith({ trust_level: 'private' })
})

it('changes message size while retaining configuration and shows an unselected computer as information', async () => {
  const sessionProfile = { templateId: 'coding', agentIds: ['plan', 'build'], agentModels: { plan: { modelId: 'model' } }, messageSize: 'standard' }
  const update = vi.fn()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(SessionSettingsFields, {
    session: { sessionProfile }, fields: ['messageSize', 'computer'], showWarning: true, onUpdate: update
  }) })
  app.use(createPinia()).use(Quasar, { components: { QSelect, QIcon, QItem, QItemLabel, QItemSection } })
  app.mount(root)
  expect(root.querySelector('.q-banner[role="status"] .text-info')?.textContent).toBe('info_outline')
  root.querySelector('.q-field__native').click()
  const option = await vi.waitUntil(() => [...document.querySelectorAll('[role="option"]')].find((item) => item.querySelector('.q-item__label')?.textContent === 'Klein'))
  option.click()
  await nextTick()
  expect(update.mock.calls[0][0].session_profile(sessionProfile)).toEqual({ ...sessionProfile, messageSize: 'small' })
})

it.each(['override', 'removed'])('lets the compact model menu return to the inherited model from %s', async (modelId) => {
  const update = vi.fn()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(SessionSettingsFields, {
    session: { agentProfileId: 'agent', sessionProfile: { agentModels: { agent: { modelId, reasoningEffort: 'high' } } } },
    fields: ['model'], compact: true, onUpdate: update
  }) })
  app.use(createPinia()).use(Quasar, { components: { QSelect, QIcon, QItem, QItemLabel, QItemSection } })
  const providers = useProviderStore()
  providers.providers = [{ id: 'provider', name: 'Local', enabled: true }]
  providers.modelDefaults = { defaultModelId: 'default' }
  providers.models = [
    { id: 'override', providerId: 'provider', modelId: 'chat', enabled: true, reasoningEfforts: ['high'] },
    { id: 'default', providerId: 'provider', modelId: 'fallback', enabled: true, reasoningEfforts: ['low'] }
  ]
  app.mount(root)
  root.querySelector('.composer-model .q-field__native').click()
  const option = await vi.waitUntil(() => [...document.querySelectorAll('[role="option"]')]
    .find((item) => item.textContent.includes('Standardmodell verwenden')))
  option.click()
  await nextTick()
  expect(update.mock.calls[0][0].session_profile({ agentModels: { agent: { modelId, reasoningEffort: 'high' } } }))
    .toEqual({ agentModels: { agent: { modelId: null, reasoningEffort: null } } })
})

it('browses the selected computer from the session directory field', async () => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  api.get.mockImplementation(async (_url, { params }) => ({ data: {
    path: params.path, hasMore: false, nextOffset: null,
    entries: params.path === '/workspace/project' ? [{ name: 'src', path: '/workspace/project/src', kind: 'directory' }] : []
  } }))
  const pinia = createPinia()
  useExecutionAgentStore(pinia).items = [{ id: 'computer', alias: 'Server', connected: true, homeDirectory: '/home/user', operations: ['filesystem.list'] }]
  const update = vi.fn()
  const session = ref({ id: 'session', preferredExecutionAgentId: 'computer', currentDirectory: '/workspace/project' })
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(SessionSettingsFields, {
    session: session.value,
    fields: ['computer'], grouped: true, onUpdate: update
  }) }).use(pinia).use(Quasar, quasarOptions)
  app.mount(root)
  root.querySelector('[aria-label="Arbeitsverzeichnis auswählen"]').click()
  const folder = await vi.waitUntil(() => [...document.querySelectorAll('.q-dialog .q-item')].find(item => item.textContent.includes('/workspace/project/src')))
  folder.click()
  await vi.waitUntil(() => document.querySelector('.q-dialog')?.textContent.includes('Keine Unterordner vorhanden.'))
  session.value = { ...session.value }
  await nextTick()
  ;[...document.querySelectorAll('.q-dialog button')].find(button => button.textContent.includes('Diesen Ordner auswählen')).click()
  expect(update).toHaveBeenCalledWith({ current_directory: '/workspace/project/src' })
})
