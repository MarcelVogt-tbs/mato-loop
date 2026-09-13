// @vitest-environment jsdom
import { createApp, h, nextTick, ref } from 'vue'
import { Quasar, QCard, QIcon, QItem, QItemLabel, QItemSection, QList, QSeparator } from 'quasar'
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
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({
    setup: () => () => h(SessionTodoStrip, {
      expanded: expanded.value,
      todos: [
        { content: 'Prüfen', status: 'completed' },
        { content: 'Umsetzen', status: 'in_progress' }
      ]
    })
  })
  app.use(Quasar, {
    components: { QCard, QIcon, QItem, QItemLabel, QItemSection, QList, QSeparator }
  })
  app.mount(root)

  expect(root.textContent).toContain('1 von 2 Aufgaben erledigt')
  expect(root.textContent).not.toContain('Prüfen')
  expect(root.querySelector('button')).toBeNull()

  expanded.value = true
  await nextTick()
  expect(root.textContent).toContain('Prüfen')
  expect(root.textContent).toContain('Umsetzen')
})
