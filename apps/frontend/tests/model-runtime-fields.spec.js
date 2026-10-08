// @vitest-environment jsdom
import { createApp, h, nextTick, reactive } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QForm } from 'quasar'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))

import ModelRuntimeFields from '../src/features/providers/ModelRuntimeFields.vue'
import { modelRuntimeForm, modelRuntimePayload } from '../src/utils/model-runtime'

let app, root
afterEach(() => { app?.unmount(); root?.remove() })

it('toggles Auto, validates manual ceilings independently and never edits them on context changes', async () => {
  const form = reactive(modelRuntimeForm({ discoveredContextWindowTokens: 4096 }))
  let qform
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(QForm, { ref: (value) => { qform = value } }, {
    default: () => h(ModelRuntimeFields, { modelValue: form })
  }) })
  app.use(Quasar, quasarOptions)
  app.mount(root)
  root.querySelector('.q-expansion-item .q-item').click()
  await nextTick()
  const toggle = [...root.querySelectorAll('.q-toggle')].find((item) => item.textContent.includes('Ausgabelimit: Auto'))
  expect(toggle.getAttribute('aria-checked')).toBe('true')
  expect(root.textContent).toContain('Provider-Ausgabelimit unbekannt')
  toggle.click()
  await nextTick()
  expect(form.maxOutputTokens).toBe(128000)
  form.maxOutputTokens = 64000
  root.querySelector('.model-runtime-context-field button').click()
  await nextTick()
  expect(form.contextWindowTokens).toBe(4096)
  expect(form.maxOutputTokens).toBe(64000)
  expect(await qform.validate()).toBe(true)
  form.maxOutputTokens = 0
  await nextTick()
  expect(await qform.validate()).toBe(false)
  expect(root.textContent).toContain('Bitte gib eine positive ganze Zahl ein.')
  toggle.click()
  form.discoveredMaxOutputTokens = 8192
  await nextTick()
  expect(modelRuntimePayload(form).max_output_tokens).toBeNull()
  expect(await qform.validate()).toBe(true)
  expect(root.textContent).toContain('Gemeldetes Provider-Ausgabelimit: 8.192 Tokens.')
  toggle.click()
  await nextTick()
  expect(form.maxOutputTokens).toBe(8192)
  form.useDiscoveredContextWindow = false
  form.contextWindowTokens = 1
  form.maxOutputTokens = null
  await nextTick()
  expect(await qform.validate()).toBe(false)
  expect(root.textContent).toContain('Das Kontextfenster muss mindestens 2 Tokens umfassen.')
  form.contextWindowTokens = 2
  await nextTick()
  expect(await qform.validate()).toBe(true)
  form.maxOutputTokens = 1
  await nextTick()
  expect(await qform.validate()).toBe(true)
})

it('shows the transport omission hint only when explicitly enabled without changing serialization', async () => {
  const form = reactive(modelRuntimeForm())
  const presentation = reactive({ reasoningSummaryForced: true })
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(ModelRuntimeFields, { modelValue: form, ...presentation }) })
  app.use(Quasar, quasarOptions)
  app.mount(root)
  root.querySelector('.q-expansion-item .q-item').click()
  await nextTick()
  const codexHint = 'Bei ChatGPT Codex wird kein Ausgabelimit übertragen. Die Einstellung beeinflusst nur die interne Kontextplanung; die Ausgabe begrenzt der Provider.'
  expect(root.textContent).not.toContain(codexHint)
  for (const cap of [null, 1024]) {
    form.maxOutputTokens = cap
    const payload = modelRuntimePayload(form)
    for (const omitted of [true, false]) {
      presentation.generationOptionsOmitted = omitted
      presentation.reasoningSummaryForced = !omitted
      await nextTick()
      expect(root.textContent.includes(codexHint)).toBe(omitted)
      expect(root.textContent.includes('Auto verwendet ein gemeldetes Providerlimit')).toBe(!omitted)
      expect(root.textContent.includes('Provider-Ausgabelimit unbekannt')).toBe(!omitted)
      if (cap !== null) {
        expect(root.textContent.includes('dient nur der internen Kontextplanung.')).toBe(omitted)
      }
      expect(modelRuntimePayload(form)).toEqual(payload)
      expect(modelRuntimePayload(form).max_output_tokens).toBe(cap)
    }
  }
})
