// @vitest-environment jsdom
import { createApp, h, ref } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import {
  Quasar, QAvatar, QBreadcrumbs, QBreadcrumbsEl, QBtn, QCard, QCardActions,
  QCardSection, QDialog, QIcon, QItem, QItemLabel, QItemSection, QLinearProgress,
  QList, QSeparator, QToolbar, Ripple
} from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn() } }))

import ExecutionAgentDirectoryPickerDialog from '../src/components/ExecutionAgentDirectoryPickerDialog.vue'
import { api } from 'src/boot/axios'

let app, root

afterEach(() => {
  app?.unmount()
  root?.remove()
  document.querySelectorAll('.q-dialog').forEach((dialog) => dialog.remove())
  vi.clearAllMocks()
})

it('browses and selects directories on the chosen execution agent', async () => {
  api.get.mockImplementation(async (_url, { params }) => ({
    data: params.path === '/home/user'
      ? {
          path: '/home/user',
          entries: [
            { name: 'projects', path: '/home/user/projects', kind: 'directory' },
            { name: 'notes.txt', path: '/home/user/notes.txt', kind: 'file' }
          ],
          hasMore: false,
          nextOffset: null
        }
      : { path: '/home/user/projects', entries: [], hasMore: false, nextOffset: null }
  }))
  const open = ref(true)
  const selected = ref(null)
  window.scrollTo = vi.fn()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({
    render: () => h(ExecutionAgentDirectoryPickerDialog, {
      modelValue: open.value,
      executionAgentId: 'agent-1',
      initialPath: '/home/user/',
      'onUpdate:modelValue': (value) => { open.value = value },
      onSelect: (value) => { selected.value = value }
    })
  })
  app.use(Quasar, {
    components: {
      QAvatar, QBreadcrumbs, QBreadcrumbsEl, QBtn, QCard, QCardActions,
      QCardSection, QDialog, QIcon, QItem, QItemLabel, QItemSection,
      QLinearProgress, QList, QSeparator, QToolbar
    },
    directives: { Ripple }
  })
  app.mount(root)

  await vi.waitFor(() => expect(document.body.textContent).toContain('/home/user/projects'))
  expect(document.body.textContent).not.toContain('notes.txt')
  expect(api.get).toHaveBeenCalledWith('/workspace/entries', expect.objectContaining({
    params: expect.objectContaining({
      execution_agent_id: 'agent-1',
      path: '/home/user'
    })
  }))

  ;[...document.querySelectorAll('.q-item')]
    .find((item) => item.textContent.includes('/home/user/projects'))
    .click()
  await vi.waitFor(() => expect(api.get).toHaveBeenLastCalledWith(
    '/workspace/entries',
    expect.objectContaining({ params: expect.objectContaining({ path: '/home/user/projects' }) })
  ))
  await vi.waitFor(() => expect(document.body.textContent).toContain('Keine Unterordner vorhanden.'))
  ;[...document.querySelectorAll('button')]
    .find((button) => button.textContent.includes('Diesen Ordner auswählen'))
    .click()

  expect(selected.value).toBe('/home/user/projects')
  expect(open.value).toBe(false)
})
