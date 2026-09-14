// @vitest-environment jsdom
import { createApp, h, nextTick, ref } from 'vue'
import { Quasar, QCard, QIcon, QLinearProgress } from 'quasar'
import { afterEach, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))

import SessionTodoStrip from '../src/components/SessionTodoStrip.vue'

let app, root
afterEach(() => {
  app?.unmount()
  root?.remove()
})

it('uses the external history state for compact and complete todo views', async () => {
  const expanded = ref(false)
  const running = ref(true)
  const todos = ref([
    { content: 'Regeln prüfen', status: 'pending' },
    { content: 'Analysieren', status: 'completed' },
    { content: 'Plan speichern', status: 'completed' },
    { content: 'Umsetzen', status: 'in_progress' }
  ])
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({
    setup: () => () => h(SessionTodoStrip, {
      expanded: expanded.value,
      running: running.value,
      todos: todos.value
    })
  })
  app.use(Quasar, { components: { QCard, QIcon, QLinearProgress } })
  app.mount(root)

  expect(root.textContent).not.toContain('Regeln prüfen')
  expect(root.textContent).not.toContain('Analysieren')
  expect(root.textContent).toContain('Umsetzen')
  expect(root.textContent).toContain('Aufgaben · 2 von 4 erledigt')
  expect(root.querySelector('[role="progressbar"]').getAttribute('aria-valuenow')).toBe('0.5')
  expect(root.querySelector('.q-card--bordered')).not.toBeNull()
  expect(root.querySelector('button')).toBeNull()

  running.value = false
  await nextTick()
  expect(root.textContent).toBe('')

  expanded.value = true
  await nextTick()
  expect(root.textContent).toContain('Regeln prüfen')
  expect(root.textContent).toContain('Analysieren')
  expect(root.textContent).toContain('Umsetzen')
  expect(root.textContent).toContain('Aufgaben · 2 von 4 erledigt')

  todos.value = todos.value.map((todo) => ({ ...todo, status: 'completed' }))
  await nextTick()
  expect(root.textContent).toContain('Aufgaben · 4 von 4 erledigt')
  expect(root.querySelector('[role="progressbar"]').getAttribute('aria-valuenow')).toBe('1')
  expect(root.querySelector('.text-positive')).not.toBeNull()
})
