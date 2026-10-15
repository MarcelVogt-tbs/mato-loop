// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { Notify, Quasar } from 'quasar'
import { afterEach, expect, it, vi } from 'vitest'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: vi.fn() }) }))

import { api } from 'src/boot/axios'
import AgentIdentitySettingsPage from '../src/features/personalization/AgentIdentitySettingsPage.vue'

let app, root
afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.clearAllMocks()
  vi.restoreAllMocks()
})

it.each([false, true])('saves the default checkbox with the persona (editing: %s)', async (editing) => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const settings = {
    scope: 'personal', effectiveDisplayName: 'MatoLoop', personalDefaultPersonaId: 'own',
    personas: [{ id: 'own', name: 'Mine', instructions: '', scope: 'personal' }]
  }
  api.get.mockResolvedValue({ data: settings })
  api.post.mockResolvedValue({ data: settings })
  api.put.mockResolvedValue({ data: settings })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: AgentIdentitySettingsPage }] })
  await router.push('/')
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(RouterView) })
  app.use(createPinia()).use(router).use(Quasar, { ...quasarOptions, plugins: { Notify } })
  app.mount(root)
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Mine bearbeiten"]')).not.toBeNull())
  const open = editing ? root.querySelector('[aria-label="Mine bearbeiten"]') : [...root.querySelectorAll('button')].find((button) => button.textContent.includes('Persona erstellen'))
  open.click()
  await vi.waitFor(() => expect(document.querySelector('[role="checkbox"]')).not.toBeNull())
  const checkbox = document.querySelector('[role="checkbox"]')
  expect(checkbox.getAttribute('aria-checked')).toBe(String(editing))
  checkbox.click()
  const name = document.querySelector('.q-dialog input')
  name.value = 'Mine'
  name.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
  document.querySelector('.q-dialog form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await vi.waitFor(() => expect(editing ? api.put : api.post).toHaveBeenCalledWith(
    editing ? '/agent-identity/personas/own' : '/agent-identity/personas',
    { name: 'Mine', instructions: '', setAsDefault: !editing }, { params: { scope: 'personal' } }
  ))
})
