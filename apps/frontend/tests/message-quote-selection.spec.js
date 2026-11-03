// @vitest-environment jsdom
import { createApp, h, nextTick, ref } from 'vue'
import { Quasar } from 'quasar'
import { afterEach, expect, it, vi } from 'vitest'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))

import MessageQuoteSelection from '../src/features/chat/components/MessageQuoteSelection.vue'
import ComposerQuote from '../src/features/chat/components/ComposerQuote.vue'
import { quotePosition } from '../src/features/chat/quote-position'

let app, root, previousMobile
afterEach(() => {
  if (previousMobile !== undefined) app.config.globalProperties.$q.screen.lt.sm = previousMobile
  app?.unmount()
  root?.remove()
  window.getSelection().removeAllRanges()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it.each([
  [false, false], [true, false], [false, true]
])('quotes a paragraph ending at the next element after pointer release (mobile=%s, cramped=%s)', async (mobile, cramped) => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const quote = vi.fn()
  root = document.createElement('div')
  root.className = 'chat-page__scroll-content'
  if (cramped) {
    root.id = 'chat-scroll-area'
    root.getBoundingClientRect = () => ({ left: 20, right: 210, top: 100, bottom: 140 })
  }
  root.innerHTML = '<div><p>Selected</p><p> answer</p></div><div></div>'
  document.body.append(root)
  let shift = 0, resizeContent
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback) { this.callback = callback }
    observe(element) { if (element === root) resizeContent = this.callback }
    disconnect() {}
  })
  const createRange = document.createRange.bind(document)
  vi.spyOn(document, 'createRange').mockImplementation(() => {
    const range = createRange()
    range.getClientRects = () => {
      if (range.startContainer.nodeType !== Node.TEXT_NODE) return [{ left: 20, right: 210, top: 100 + shift, bottom: 140 + shift, width: 190, height: 40 }]
      const first = range.startContainer === root.firstChild.firstChild.firstChild
      return [{ left: 170, right: first ? 190 : 200, top: (first ? 100 : 120) + shift, bottom: (first ? 120 : 140) + shift, width: first ? 20 : 30, height: 20 }]
    }
    return range
  })
  app = createApp({ render: () => h(MessageQuoteSelection, { target: root.firstChild, onQuote: quote }) })
  app.use(Quasar, quasarOptions)
  previousMobile = app.config.globalProperties.$q.screen.lt.sm
  app.config.globalProperties.$q.screen.lt.sm = mobile
  app.mount(root.lastChild)
  const range = document.createRange()
  range.selectNodeContents(root.firstChild)
  range.setEnd(root.lastChild, 0)
  root.firstChild.getBoundingClientRect = () => ({ left: 20, right: 210, width: 190 })
  vi.spyOn(HTMLButtonElement.prototype, 'getBoundingClientRect').mockReturnValue({
    left: 122, right: 202, top: 68, bottom: 100, width: 80, height: 32
  })
  document.dispatchEvent(new MouseEvent('pointerdown', { button: 0 }))
  window.getSelection().addRange(range)
  document.dispatchEvent(new Event('selectionchange'))
  await new Promise(resolve => setTimeout(resolve, 180))
  expect(document.querySelector('[aria-label="Auswahl zitieren"]')).toBeNull()
  document.dispatchEvent(new Event('pointerup'))
  await vi.waitUntil(() => document.querySelector('[aria-label="Auswahl zitieren"]'))
  const position = document.querySelector('.chat-quote-selection').style
  expect([position.left, position.top]).toEqual(['122px', cramped ? '92px' : '60px'])
  document.querySelector('[aria-label="Auswahl zitieren"]').click()
  const input = await vi.waitUntil(() => document.querySelector('[aria-label="Kommentar zum Zitat"]'))
  expect(!!document.querySelector('.q-dialog')).toBe(mobile || cramped)
  if (!mobile && !cramped) {
    expect(document.querySelector('.chat-quote-highlight')).not.toBeNull()
    shift = 100
    resizeContent()
    await vi.waitFor(() => expect(document.querySelector('.chat-quote-selection').style.top).toBe('160px'))
  }
  input.value = 'Please explain this.'
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
  expect(quote).not.toHaveBeenCalled()
  document.querySelector('[aria-label="Zitat in den Entwurf übernehmen"]').click()
  await vi.waitFor(() => expect(quote).toHaveBeenCalledWith({ text: 'Selected answer', comment: 'Please explain this.' }))
})

it.each([
  [100, 140, true, 32, '60px', '84px'],
  [100, 140, false, 32, '148px', '144px'],
  [10, 50, true, 32, '58px', '234px'],
  [250, 290, false, 32, '210px', '234px'],
  [145, 165, false, 212, '8px', '129px']
])('centers the popup and limits it to an available side (%s, %s, %s)', (top, bottom, preferAbove, height, expectedTop, maxHeight) => {
  expect(quotePosition(
    { left: 20, right: 320, top: 0, bottom: 300 },
    { left: 170, right: 200, top, bottom, preferAbove },
    { width: 80, height }
  )).toEqual({ left: '145px', top: expectedTop, maxHeight })
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
