// @vitest-environment jsdom
import { createApp, h, nextTick, ref } from 'vue'
import { Quasar, QCard, QIcon, QSeparator } from 'quasar'
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
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({
    setup: () => () => h(SessionTodoStrip, {
      expanded: expanded.value,
      running: running.value,
      todos: [
        { content: 'Prüfen', status: 'completed' },
        { content: 'Umsetzen', status: 'in_progress' }
      ]
    })
  })
  app.use(Quasar, { components: { QCard, QIcon, QSeparator } })
  app.mount(root)

  expect(root.textContent).not.toContain('Prüfen')
  expect(root.textContent).toContain('Umsetzen')
  expect(root.textContent).toContain('Aufgaben · 2/2')
  expect(root.querySelector('.q-card--bordered')).not.toBeNull()
  expect(root.querySelector('button')).toBeNull()

  running.value = false
  await nextTick()
  expect(root.textContent).toBe('')

  expanded.value = true
  await nextTick()
  expect(root.textContent).toContain('Prüfen')
  expect(root.textContent).toContain('Umsetzen')
  expect(root.textContent).toContain('Aufgaben · 2/2')
})
