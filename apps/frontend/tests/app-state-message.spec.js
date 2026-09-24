// @vitest-environment jsdom
import { createApp, h, nextTick, reactive } from 'vue'
import { Quasar, QBtn, QIcon } from 'quasar'
import { afterEach, expect, it, vi } from 'vitest'
import AppStateMessage from '../src/shared/ui/AppStateMessage.vue'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))

let app, root
afterEach(() => {
  app?.unmount()
  root?.remove()
  document.body.classList.remove('body--dark', 'body--light')
})

it.each([false, true])('renders optional content, actions and caller attributes (dark=%s)', async (dark) => {
  const state = reactive({ icon: 'cloud_off', title: 'Nicht erreichbar', description: '<b>Erneut versuchen.</b>', iconColor: '', role: 'alert', actions: true })
  const retry = vi.fn()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({
    render: () => h(AppStateMessage, {
      icon: state.icon, title: state.title, description: state.description, iconColor: state.iconColor,
      role: state.role, class: 'fit q-pa-xl', 'aria-label': 'Vorschau'
    }, state.actions ? {
      actions: () => [h(QBtn, { label: 'Erneut versuchen', onClick: retry }), h(QBtn, { label: 'Abbrechen' })]
    } : {})
  })
  app.use(Quasar, { components: { QBtn, QIcon }, config: { dark } })
  app.config.globalProperties.$q.dark.set(dark)
  app.mount(root)

  const message = root.querySelector('.app-state-message')
  expect(message.getAttribute('role')).toBe('alert')
  expect(message.getAttribute('aria-label')).toBe('Vorschau')
  expect(message.classList.contains('fit')).toBe(true)
  expect(message.querySelector('.q-icon').style.fontSize).toBe('48px')
  expect(message.querySelector('.q-icon').getAttribute('aria-hidden')).toBe('true')
  expect(message.querySelector('.q-icon').classList.contains('app-muted')).toBe(true)
  expect(message.querySelector('.text-subtitle1').textContent).toBe(state.title)
  expect(message.querySelector('.text-body2').textContent).toBe(state.description)
  expect(message.querySelector('b')).toBeNull()
  expect(message.querySelectorAll('button')).toHaveLength(2)
  message.querySelector('button').click()
  expect(retry).toHaveBeenCalledOnce()

  state.iconColor = 'negative'
  await nextTick()
  expect(message.querySelector('.q-icon').classList.contains('text-negative')).toBe(true)
  expect(message.querySelector('.q-icon').classList.contains('app-muted')).toBe(false)

  Object.assign(state, { icon: '', title: '', role: undefined, actions: false })
  await nextTick()
  expect(message.hasAttribute('role')).toBe(false)
  expect(message.querySelector('.q-icon')).toBeNull()
  expect(message.querySelector('.text-subtitle1')).toBeNull()
  expect(message.querySelector('.q-mt-md')).toBeNull()
  expect(message.querySelector('.text-body2').textContent).toBe(state.description)

  Object.assign(state, { title: 'Keine Vorschau', description: '' })
  await nextTick()
  expect(message.querySelector('.text-body2')).toBeNull()
  expect(message.querySelector('.text-subtitle1').textContent).toBe(state.title)
})
