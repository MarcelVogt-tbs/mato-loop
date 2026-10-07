// @vitest-environment jsdom
import { createApp, h, ref } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, Dialog, Notify, QDialog, QCard, QForm, QCardSection, QInput, QSeparator, QSelect, QItem, QItemSection, QIcon, QItemLabel, QBtn, QTooltip, QCardActions, ClosePopup } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }))
vi.mock('src/features/computers/ExecutionAgentDirectoryPickerDialog.vue', () => ({ default: { render: () => null } }))
import ProjectDialog from '../src/features/projects/ProjectDialog.vue'
import { api } from '../src/boot/axios'

let app, root
afterEach(() => { app?.unmount(); root?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it.each([false, true])('saves project fields through the shared dialog (editing=%s)', async (editing) => {
  vi.stubGlobal('scrollTo', vi.fn())
  api.get.mockImplementation(url => Promise.resolve({ data: url === '/session-templates'
    ? { templates: [{ id: 'coding', name: 'Coding' }] }
    : { items: [{ id: 'computer', alias: 'Rechner', connected: true, operations: [], homeDirectory: '/home/user' }] } }))
  const project = { id: 'project', name: 'Bestehend', instructions: 'Anweisungen', defaultSessionTemplateId: 'coding', defaultExecutionAgentId: 'computer', defaultCurrentDirectory: '/home/user/project' }
  const request = editing ? api.patch : api.post
  request.mockReset().mockRejectedValueOnce(new Error('unavailable')).mockResolvedValue({ data: project })
  const open = ref(true), created = vi.fn()
  root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(ProjectDialog, { modelValue: open.value, 'onUpdate:modelValue': value => { open.value = value }, project: editing ? project : null, onCreated: created }) })
  app.use(createPinia()).use(Quasar, { components: { QDialog, QCard, QForm, QCardSection, QInput, QSeparator, QSelect, QItem, QItemSection, QIcon, QItemLabel, QBtn, QTooltip, QCardActions }, directives: { ClosePopup }, plugins: { Dialog, Notify } })
  app.mount(root)
  await vi.waitFor(() => expect(document.querySelector('.q-dialog input')).not.toBeNull())
  const name = document.querySelector('.q-dialog input')
  name.value = 'Umbenannt'
  name.dispatchEvent(new Event('input', { bubbles: true }))
  const save = () => document.querySelector('.q-dialog form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  save()
  await vi.waitFor(() => expect(request).toHaveBeenCalledOnce())
  expect(open.value).toBe(true)
  expect(name.value).toBe('Umbenannt')
  save()
  await vi.waitFor(() => expect(open.value).toBe(false))
  expect(request).toHaveBeenLastCalledWith(editing ? '/projects/project' : '/projects', {
    name: 'Umbenannt', instructions: editing ? 'Anweisungen' : '',
    defaultSessionTemplateId: editing ? 'coding' : null,
    defaultExecutionAgentId: editing ? 'computer' : null,
    defaultCurrentDirectory: editing ? '/home/user/project' : null
  })
  if (editing) expect(created).not.toHaveBeenCalled()
  else expect(created).toHaveBeenCalledWith(project)
})
