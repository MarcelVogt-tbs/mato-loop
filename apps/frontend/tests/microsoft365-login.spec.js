// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Quasar } from 'quasar'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { post: vi.fn(), delete: vi.fn(), get: vi.fn() } }))
const { confirm } = vi.hoisted(() => ({ confirm: vi.fn() }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm }) }))

import Login from '../src/features/integrations/Microsoft365Login.vue'
import { useIntegrationStore } from '../src/stores/integrations'
import { api } from 'src/boot/axios'

let app, root
beforeEach(() => {
  vi.resetAllMocks()
  vi.useFakeTimers()
  confirm.mockResolvedValue(true)
})
afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.useRealTimers()
})

async function mount(integration = {}, handlers = {}) {
  const pinia = createPinia()
  const store = useIntegrationStore(pinia)
  store.selectScope('personal')
  store.microsoft365.accounts = [{ id: 'work', authStatus: 'authorization_required', ...integration }]
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(Login, {
    integration: store.microsoft365.accounts[0],
    ...handlers
  }) }).use(pinia).use(Quasar, quasarOptions)
  app.config.globalProperties.$q.notify = vi.fn()
  app.mount(root)
  await nextTick()
  return store
}

function button(label) {
  return [...root.querySelectorAll('button')].find((item) => item.textContent.includes(label))
}

it.each(['device_code', 'browser'])('completes %s login only after the server authorizes it', async (flow) => {
  const changed = vi.fn()
  api.post.mockImplementation(async (url) => ({ data: url.endsWith('/login') ? {
    id: 'attempt', interval: 5, expiresIn: 600,
    authorizationUrl: flow === 'device_code' ? 'https://microsoft.com/devicelogin' : 'https://login.microsoftonline.com/tenant/oauth2/v2.0/authorize',
    ...(flow === 'device_code' ? { userCode: 'CODE123' } : { redirectUri: 'https://matoloop.test/api/integrations/microsoft365/oauth/callback' })
  } : { status: 'authorized' } }))
  await mount({}, { onChanged: changed })
  button('Bei Microsoft anmelden').click()
  await vi.advanceTimersByTimeAsync(0)
  expect(root.querySelector('a').getAttribute('rel')).toBe('noopener noreferrer')
  expect(changed).not.toHaveBeenCalled()
  expect(root.textContent).toContain(flow === 'device_code' ? 'CODE123' : 'https://matoloop.test/api/integrations/microsoft365/oauth/callback')
  await vi.advanceTimersByTimeAsync(5000)
  expect(api.post).toHaveBeenCalledWith('/integrations/microsoft365/work/login', null, { params: { scope: 'personal' } })
  expect(api.post).toHaveBeenCalledWith('/integrations/microsoft365/oauth/attempts/attempt/finish')
  expect(changed).toHaveBeenCalledOnce()
})

it('retains a pending login when cancellation fails and stops polling after cancellation succeeds', async () => {
  api.post.mockResolvedValue({ data: {
    id: 'attempt', interval: 5, expiresIn: 600, userCode: 'CODE123', authorizationUrl: 'https://microsoft.com/devicelogin'
  } })
  api.delete.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({})
  await mount()
  button('Bei Microsoft anmelden').click()
  await vi.advanceTimersByTimeAsync(0)
  button('Anmeldung abbrechen').click()
  await vi.advanceTimersByTimeAsync(0)
  expect(root.textContent).toContain('CODE123')
  button('Anmeldung abbrechen').click()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(root.textContent).not.toContain('CODE123')
  expect(api.post).toHaveBeenCalledTimes(1)
})

it('keeps the existing connection visible after a failed disconnect', async () => {
  api.post.mockRejectedValue(new Error('offline'))
  const changed = vi.fn()
  await mount({ hasCredential: true, authStatus: 'authorized' }, { onChanged: changed })
  button('Verbindung trennen').click()
  await vi.advanceTimersByTimeAsync(0)
  expect(confirm).toHaveBeenCalledOnce()
  expect(changed).not.toHaveBeenCalled()
  expect(root.textContent).toContain('Microsoft 365 ist verbunden.')
})

it('shows a successful disconnect even when the subsequent refresh fails', async () => {
  api.post.mockResolvedValue({})
  api.get.mockRejectedValue(new Error('offline'))
  const store = await mount({ hasCredential: true, authStatus: 'authorized' }, {
    onChanged: () => { void store.fetchMicrosoft365().catch(() => {}) }
  })
  button('Verbindung trennen').click()
  await vi.advanceTimersByTimeAsync(0)
  expect(root.textContent).toContain('Eine Microsoft-Anmeldung ist erforderlich.')
})

it('stops polling when its settings page is closed', async () => {
  api.post.mockResolvedValue({ data: {
    id: 'attempt', interval: 5, expiresIn: 600, authorizationUrl: 'https://microsoft.com/devicelogin'
  } })
  await mount()
  button('Bei Microsoft anmelden').click()
  await vi.advanceTimersByTimeAsync(0)
  app.unmount()
  app = null
  await vi.advanceTimersByTimeAsync(10_000)
  expect(api.post).toHaveBeenCalledTimes(1)
})
