// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { Quasar, QCard, QDialog } from 'quasar'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))

import MessageContent from '../src/components/MessageContent.vue'

let app, root

afterEach(() => {
  app?.unmount()
  root?.remove()
})

describe('prepared Markdown replies', () => {
  it('emits only a matching host-provided reply', () => {
    const reply = vi.fn()
    root = document.createElement('div')
    document.body.append(root)
    app = createApp({
      render: () => h(MessageContent, {
        content: '[Kunde 123](#reply-customer-123)',
        preparedReplies: [{ id: 'customer-123', content: 'Verwende ERP-Kunde 123.' }],
        onPreparedReply: reply
      })
    })
    app.use(Quasar, { components: { QCard, QDialog } })
    app.mount(root)

    root.querySelector('a').click()

    expect(reply).toHaveBeenCalledOnce()
    expect(reply).toHaveBeenCalledWith('Verwende ERP-Kunde 123.')
  })
})
