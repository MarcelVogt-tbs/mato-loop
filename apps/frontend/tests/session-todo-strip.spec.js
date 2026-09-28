// @vitest-environment jsdom
import { createApp, h, nextTick, ref } from 'vue'
import { Quasar, QCard, QIcon, QLinearProgress } from 'quasar'
import { afterEach, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))

import SessionTodoStrip from '../src/features/chat/components/SessionTodoStrip.vue'

let app, root
afterEach(() => {
  app?.unmount()
  root?.remove()
})

it('shows every task and updates progress as tasks change', async () => {
  const todos = ref([
    { content: 'Regeln prüfen', status: 'pending' },
    { content: 'Analysieren', status: 'completed' },
    { content: 'Plan speichern', status: 'completed' },
    { content: 'Umsetzen', status: 'in_progress' }
  ])
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({
    setup: () => () => h(SessionTodoStrip, { todos: todos.value })
  })
  app.use(Quasar, { components: { QCard, QIcon, QLinearProgress } })
  app.mount(root)

  expect(root.textContent).toContain('Regeln prüfen')
  expect(root.textContent).toContain('Analysieren')
  expect(root.textContent).toContain('Umsetzen')
  expect(root.querySelectorAll('[role="listitem"]')).toHaveLength(4)
  expect(root.textContent).toContain('Aufgaben · 2 von 4 erledigt')
  expect(root.querySelector('[role="progressbar"]').getAttribute('aria-valuenow')).toBe('0.5')
  expect(root.querySelector('button').getAttribute('aria-expanded')).toBe('true')

  todos.value = todos.value.map((todo) => ({ ...todo, status: 'completed' }))
  await nextTick()
  expect(root.textContent).toContain('Aufgaben · 4 von 4 erledigt')
  expect(root.querySelector('[role="progressbar"]').getAttribute('aria-valuenow')).toBe('1')
  expect(root.querySelector('.text-positive')).not.toBeNull()

  todos.value = []
  await nextTick()
  expect(root.querySelector('.session-todos')).toBeNull()
})

it('toggles the controlled collapsed state without hiding progress', async () => {
  const collapsed = ref(false)
  const todos = ref([{ content: 'Prüfen', status: 'pending' }])
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({
    setup: () => () => h(SessionTodoStrip, {
      todos: todos.value,
      collapsed: collapsed.value,
      'onUpdate:collapsed': (value) => { collapsed.value = value }
    })
  })
  app.use(Quasar)
  app.mount(root)

  const button = root.querySelector('button')
  const list = root.querySelector('[role="list"]')
  expect(button.getAttribute('aria-controls')).toBe(list.id)
  button.click()
  await nextTick()
  expect(list.style.display).toBe('none')
  expect(button.getAttribute('aria-expanded')).toBe('false')

  todos.value = [{ content: 'Prüfen', status: 'completed' }]
  await nextTick()
  expect(list.style.display).toBe('none')
  expect(root.textContent).toContain('1 von 1 erledigt')
  expect(root.querySelector('[role="progressbar"]')).not.toBeNull()

  button.click()
  await nextTick()
  expect(list.style.display).not.toBe('none')
  expect(button.getAttribute('aria-expanded')).toBe('true')
  expect(root.querySelector('.text-strike').textContent).toBe('Prüfen')
})
