// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { Notify, Quasar } from 'quasar'
import { afterEach, expect, it, vi } from 'vitest'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
const chat = vi.hoisted(() => ({ loadSession: vi.fn(), send: vi.fn(), current: null }))
vi.mock('src/features/chat/store', () => ({ useChatStore: () => chat }))
import { api } from 'src/boot/axios'
import AssistantSettings from '../src/features/personalization/AssistantSettings.vue'

let app, root, router
const initial = { enabled: false, initialized: false, sessionId: null, automationId: null }
const button = label => [...root.querySelectorAll('button')].find(item => item.textContent.includes(label))
async function mount(settings = initial, automation = null) {
  api.get.mockImplementation(async path => ({ data: path === '/assistant' ? settings : automation }))
  router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: AssistantSettings },
    { path: '/chat', name: 'chat', component: { template: '<div>Chat</div>' } },
    { path: '/automations/:automationId', name: 'settings-automation-detail', component: { template: '<div />' } },
    { path: '/skills/:skillId', name: 'settings-skill-edit', component: { template: '<div />' } }
  ] })
  await router.push('/')
  root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(RouterView) })
  app.use(createPinia()).use(router).use(Quasar, { ...quasarOptions, plugins: { Notify } })
  app.mount(root)
  await vi.waitFor(() => expect(button(settings.initialized ? 'Einrichtung anpassen' : 'Einrichten')).toBeDefined())
}
afterEach(() => { app?.unmount(); root?.remove(); vi.resetAllMocks(); chat.current = null })

it('requires setup before exposing activation, including an existing uninitialized automation', async () => {
  await mount({ ...initial, sessionId: 'session', automationId: 'automation' })
  expect(root.querySelector('[role="switch"]')).toBeNull()
  expect(root.textContent).toContain('Nicht eingerichtet')
  expect(api.put).not.toHaveBeenCalled()
  expect(root.querySelector('a[href="/skills/00000000-0000-4000-8000-000000000103"]')).not.toBeNull()
})

it('ensures the assigned session, sends the normal setup command without consuming drafts, and opens chat', async () => {
  await mount()
  api.post.mockResolvedValue({ data: { ...initial, sessionId: 'assistant-session', automationId: 'automation' } })
  chat.loadSession.mockImplementation(async id => { chat.current = { id } })
  chat.send.mockResolvedValue({ sessionId: 'assistant-session' })
  button('Einrichten').click()
  await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('chat'))
  expect(api.post).toHaveBeenCalledWith('/assistant/setup')
  expect(chat.loadSession).toHaveBeenCalledWith('assistant-session')
  expect(chat.send).toHaveBeenCalledWith('/assistant-init', { preserveDraft: true, activeFile: null })
  expect(router.currentRoute.value.query).toEqual({ session: 'assistant-session' })
})

it('enables and pauses through the automation store, preserving the latest definition and revision', async () => {
  const schedules = [
    { cron: '13 8 * * 1-5', timezone: 'Europe/Berlin', validUntil: '2027-02-01T10:00:00+01:00', enabled: true },
    { runAt: '2027-01-01T08:00:00.123456+01:00', enabled: false }
  ]
  const saved = { id: 'automation', name: 'Custom assistant', task: 'Custom task', modelId: 'model',
    triggers: ['agent_response'], schedules, revision: 7, enabled: false }
  await mount({ ...initial, initialized: true, sessionId: 'session', automationId: saved.id }, saved)
  api.put.mockImplementation(async (_, payload) => {
    Object.assign(saved, { enabled: payload.enabled, revision: saved.revision + 1 })
    return { data: { ...saved } }
  })
  for (const enabled of [true, false]) {
    const expectedRevision = saved.revision
    root.querySelector('[role="switch"]').click()
    await vi.waitFor(() => expect(root.textContent).toContain(enabled ? 'Aktiv' : 'Pausiert'))
    expect(api.get).toHaveBeenCalledWith('/automations/automation', { params: { includeHistory: false } })
    expect(api.put).toHaveBeenLastCalledWith('/automations/automation', {
      name: saved.name, task: saved.task, enabled, modelId: saved.modelId,
      schedules, triggers: saved.triggers, expectedRevision
    })
  }
})
