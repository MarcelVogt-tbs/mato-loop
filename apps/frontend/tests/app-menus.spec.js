// @vitest-environment jsdom
import { createApp, h, nextTick, ref, withDirectives } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QBtn, QForm, QIcon, QItem, QItemLabel, QItemSection, QList, QMenu, QSelect, ClosePopup } from 'quasar'
import AppMenu from '../src/shared/ui/AppMenu.vue'
import AppSelect from '../src/shared/ui/AppSelect.vue'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))

let app, root
afterEach(() => { app?.unmount(); root?.remove(); vi.restoreAllMocks() })

function mount(render) {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render })
  app.use(Quasar, { components: { QBtn, QForm, QIcon, QItem, QItemLabel, QItemSection, QList, QMenu, QSelect } })
  app.mount(root)
}

it('forwards single and multiple selection and retains disabled options', async () => {
  const single = ref(null), multiple = ref([])
  const options = [{ label: 'Alpha', value: 'a', caption: 'Anbieter', icon: 'public' }, { label: 'Gesperrt', value: 'b', disable: true }]
  mount(() => h('div', [
    h(AppSelect, { label: 'Einzeln', modelValue: single.value, options, emitValue: true, mapOptions: true, 'onUpdate:modelValue': value => { single.value = value } }),
    h(AppSelect, { label: 'Mehrfach', modelValue: multiple.value, options, multiple: true, useChips: true, emitValue: true, mapOptions: true, 'onUpdate:modelValue': value => { multiple.value = value } })
  ]))
  root.querySelector('[aria-label="Einzeln"]').click()
  await vi.waitFor(() => expect(document.querySelector('[role="option"][aria-disabled="true"]')).not.toBeNull())
  document.querySelector('[role="option"][aria-disabled="true"]').click()
  expect(single.value).toBeNull()
  document.querySelector('[role="option"]').click()
  await vi.waitFor(() => expect(single.value).toBe('a'))
  await vi.waitFor(() => expect(document.querySelector('[role="listbox"]')).toBeNull())
  root.querySelector('[aria-label="Mehrfach"]').click()
  await vi.waitFor(() => expect(document.querySelector('[role="option"]')).not.toBeNull())
  document.querySelector('[role="option"]').click()
  await nextTick()
  expect(multiple.value).toEqual(['a'])
  document.querySelector('[role="option"]').click()
  await nextTick()
  expect(multiple.value).toEqual([])
})

it('forwards custom option, selected and empty slots and Quasar form validation', async () => {
  let form
  const value = ref(null), options = ref([{ label: 'Alpha', value: 'a' }])
  mount(() => h(QForm, { ref: instance => { form = instance } }, () => h(AppSelect, {
    label: 'Auswahl', modelValue: value.value, options: options.value, emitValue: true, mapOptions: true,
    rules: [value => Boolean(value) || 'Pflichtfeld'], 'onUpdate:modelValue': selected => { value.value = selected }
  }, {
    selected: () => h('span', 'Eigene Auswahl'),
    option: scope => h(QItem, scope.itemProps, () => 'Eigene Option'),
    'no-option': () => h('span', 'Keine Optionen')
  })))
  expect(await form.validate()).toBe(false)
  root.querySelector('[role="combobox"]').click()
  const option = await vi.waitUntil(() => document.querySelector('[role="option"]'))
  expect(option.textContent).toBe('Eigene Option')
  option.click()
  await vi.waitFor(() => expect(value.value).toBe('a'))
  expect(await form.validate()).toBe(true)
  expect(root.textContent).toContain('Eigene Auswahl')
  options.value = []
  await nextTick()
  root.querySelector('[role="combobox"]').click()
  await vi.waitFor(() => expect(document.querySelector('[role="listbox"]')?.textContent).toBe('Keine Optionen'))
})

it('keeps a nested selection inside its action menu and forwards close-popup', async () => {
  const open = ref(false)
  mount(() => h(QBtn, { label: 'Aktionen' }, () => h(AppMenu, {
    modelValue: open.value, 'onUpdate:modelValue': value => { open.value = value }
  }, () => h(QList, null, () => [
    h(QItem, null, () => h(AppSelect, { label: 'Auswahl', modelValue: null, options: ['Alpha'] })),
    withDirectives(h(QItem, { clickable: true, 'aria-label': 'Schließen' }, () => 'Schließen'), [[ClosePopup]])
  ]))))
  root.querySelector('button').click()
  await vi.waitFor(() => expect(open.value).toBe(true))
  document.querySelector('[role="combobox"]').click()
  const option = await vi.waitUntil(() => document.querySelector('[role="option"]'))
  option.click()
  await vi.waitFor(() => expect(document.querySelector('[role="listbox"]')).toBeNull())
  expect(open.value).toBe(true)
  document.querySelector('[aria-label="Schließen"]').click()
  await vi.waitFor(() => expect(open.value).toBe(false))
})
