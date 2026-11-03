// @vitest-environment jsdom
import { createApp, h, nextTick, ref } from 'vue'
import { Quasar } from 'quasar'
import { afterEach, expect, it, vi } from 'vitest'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))

import MessageQuoteSelection from '../src/features/chat/components/MessageQuoteSelection.vue'
import ComposerQuote from '../src/features/chat/components/ComposerQuote.vue'

let app, root, previousMobile
afterEach(() => {
  if (previousMobile !== undefined) app.config.globalProperties.$q.screen.lt.sm = previousMobile
  app?.unmount()
  root?.remove()
  window.getSelection().removeAllRanges()
  vi.restoreAllMocks()
})

it.each([false, true])('uses an inline editor on desktop and a dialog on mobile (%s)', async mobile => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const quote = vi.fn()
  root = document.createElement('div')
  root.innerHTML = '<p>Selected answer</p><div></div>'
  document.body.append(root)
  app = createApp({ render: () => h(MessageQuoteSelection, { target: root.firstChild, onQuote: quote }) })
  app.use(Quasar, quasarOptions)
  previousMobile = app.config.globalProperties.$q.screen.lt.sm
  app.config.globalProperties.$q.screen.lt.sm = mobile
  app.mount(root.lastChild)
  const range = document.createRange()
  range.selectNodeContents(root.firstChild)
  range.getBoundingClientRect = () => ({ left: 170, top: 100, bottom: 140, right: 200 })
  root.firstChild.getBoundingClientRect = () => ({ left: 20, right: 210, width: 190 })
  range.cloneRange = () => range
  range.getClientRects = () => [{ left: 170, top: 100, right: 200, bottom: 140, width: 30, height: 40 }]
  vi.spyOn(HTMLButtonElement.prototype, 'getBoundingClientRect').mockReturnValue({
    left: 122, right: 202, top: 68, bottom: 100, width: 80, height: 32
  })
  window.getSelection().addRange(range)
  document.dispatchEvent(new Event('selectionchange'))
  await nextTick()
  await nextTick()
  const position = document.querySelector('.chat-quote-selection').style
  expect([position.left, position.bottom]).toEqual(['122px', `${window.innerHeight - 96}px`])
  document.querySelector('[aria-label="Auswahl zitieren"]').click()
  const input = await vi.waitUntil(() => document.querySelector('[aria-label="Kommentar zum Zitat"]'))
  expect(!!document.querySelector('.q-dialog')).toBe(mobile)
  if (!mobile) expect(document.querySelector('.chat-quote-highlight')).not.toBeNull()
  input.value = 'Please explain this.'
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
  expect(quote).not.toHaveBeenCalled()
  document.querySelector('[aria-label="Zitat in den Entwurf übernehmen"]').click()
  await vi.waitFor(() => expect(quote).toHaveBeenCalledWith({ text: 'Selected answer', comment: 'Please explain this.' }))
})

it.each([false, true])('edits quotes outside the card and closes the editor when the draft quote is replaced (%s)', async mobile => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  root = document.createElement('div')
  document.body.append(root)
  const quote = ref({ text: 'Long source text', comment: 'My question' })
  app = createApp({ render: () => h(ComposerQuote, { quote: quote.value }) })
  app.use(Quasar, quasarOptions)
  previousMobile = app.config.globalProperties.$q.screen.lt.sm
  app.config.globalProperties.$q.screen.lt.sm = mobile
  app.mount(root)
  expect([root.querySelector('[aria-expanded]'), root.querySelector('.composer-quote__comment').textContent]).toEqual([null, 'My question'])
  root.querySelector('[aria-label="Zitat bearbeiten"]').click()
  const input = await vi.waitUntil(() => document.querySelector('textarea'))
  expect([root.querySelector('textarea'), input.value]).toEqual([null, 'Long source text'])
  quote.value = { text: 'Restored queued quote', comment: 'Restored comment' }
  await vi.waitFor(() => expect(document.querySelector('textarea')).toBeNull())
})

it('shows sent quote text only once', () => {
  previousMobile = undefined
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(ComposerQuote, { quote: { text: 'Source text', comment: '' }, editable: false }) })
  app.use(Quasar, quasarOptions).mount(root)
  expect(root.textContent.match(/Source text/gu)).toHaveLength(1)
})
