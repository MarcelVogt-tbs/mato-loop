// @vitest-environment jsdom
import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { Quasar } from 'quasar'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: vi.fn(async () => true) }) }))

import MailApp from '../src/features/mail/MailApp.vue'
import { useMailStore } from '../src/features/mail/store'
import { api } from 'src/boot/axios'

let app, root, account
beforeEach(() => {
  vi.resetAllMocks()
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  account = {
    integrationId: 'work', name: 'Work', integrationType: 'microsoft365',
    capabilities: { createDraft: true, editDraft: false, send: false, delete: false, ccBcc: false, uploadAttachments: false }
  }
  api.get.mockImplementation(async (path) => ({ data: path === '/mail/accounts'
    ? { accounts: [account] }
    : path.endsWith('/mailboxes')
      ? { mailboxes: [{ key: 'inbox', name: 'Posteingang', role: 'inbox' }] }
      : path.endsWith('/messages')
        ? { messages: [{ messageToken: 'message', subject: 'Subject', isDraft: true }] }
        : { message: { messageToken: path.split('/').at(-1), subject: 'Subject', body: 'Message', isDraft: true, version: 'version' } }
  }))
  api.post.mockResolvedValue({ data: { messageToken: 'draft', isDraft: true } })
})
afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.restoreAllMocks()
})

async function mount() {
  root = document.createElement('div')
  document.body.append(root)
  app = createApp(MailApp).use(createPinia()).use(Quasar, quasarOptions)
  app.config.globalProperties.$q.notify = vi.fn()
  app.mount(root)
  await vi.waitFor(() => expect(root.textContent).toContain('Subject'))
}

function button(label) {
  return [...document.querySelectorAll('button')].find((item) => item.textContent.trim().endsWith(label))
}

async function openComposer() {
  root.querySelector('[aria-label="E-Mail verfassen"]').click()
  await vi.waitFor(() => expect(button('Als Entwurf speichern')).toBeTruthy())
}

async function fill(label, value) {
  const field = [...document.querySelectorAll('.q-field')].find((item) => item.querySelector('.q-field__label')?.textContent === label)
  const input = field.querySelector('input, textarea')
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
}

it.each([true, false])('uses Microsoft 365 capabilities when drafts are %s', async (enabled) => {
  account.capabilities.createDraft = enabled
  await mount()
  expect(root.querySelector('[aria-label="E-Mail verfassen"]').disabled).toBe(!enabled)
  root.querySelector('.q-item--clickable').click()
  await vi.waitFor(() => expect(root.textContent).toContain('Von:'))
  expect(root.querySelector('[aria-label="Entwurf bearbeiten"], [aria-label="E-Mail löschen"]')).toBeNull()
  if (!enabled) return
  await openComposer()
  expect(button('Senden')).toBeUndefined()
  expect(document.querySelector('input[type="file"]')).toBeNull()
  await fill('An', 'recipient@example.com')
  await fill('Betreff', 'Test')
  await fill('Nachricht', 'Text')
  button('Als Entwurf speichern').click()
  await vi.waitFor(() => expect(api.post).toHaveBeenCalledOnce())
  const [path, data] = api.post.mock.calls[0]
  expect(path).toBe('/mail/accounts/work/drafts')
  expect(data.getAll('to')).toEqual(['recipient@example.com'])
  await vi.waitFor(() => expect(button('Als Entwurf speichern')).toBeUndefined())
})

it('keeps sending and editing available for IMAP/SMTP', async () => {
  account.integrationType = 'mail'
  Object.keys(account.capabilities).forEach((key) => { account.capabilities[key] = true })
  await mount()
  root.querySelector('.q-item--clickable').click()
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Entwurf bearbeiten"]')).toBeTruthy())
  expect(root.querySelector('[aria-label="E-Mail löschen"]')).toBeTruthy()
  await openComposer()
  expect(button('Senden')).toBeTruthy()
  expect(document.querySelector('input[type="file"]')).toBeTruthy()
})

it('blocks repeat draft submission after an uncertain Graph outcome', async () => {
  api.post.mockRejectedValue({ response: { data: { error: { code: 'integration.outcomeUnknown' } } } })
  await mount()
  await openComposer()
  await fill('An', 'recipient@example.com')
  await fill('Nachricht', 'Text')
  button('Als Entwurf speichern').click()
  await vi.waitFor(() => expect(document.body.textContent).toContain('Prüfe zuerst den Entwurfsordner'))
  expect(button('Als Entwurf speichern').disabled).toBe(true)
  const form = document.querySelector('.q-dialog form')
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await nextTick()
  expect(api.post).toHaveBeenCalledOnce()
})

it('sends Microsoft mail with Cc, Bcc, local files and a safe signature preview', async () => {
  Object.assign(account.capabilities, { send: true, delete: true, ccBcc: true, uploadAttachments: true })
  account.signatureHtml = '<b>Team</b><img src="https://example.com/tracker.png" onerror="alert(1)">'
  api.post.mockResolvedValue({ data: { sent: true, sentCopyConfirmed: null } })
  await mount()
  await openComposer()
  await fill('An', 'to@example.com')
  await fill('Cc', 'cc@example.com')
  await fill('Bcc', 'bcc@example.com')
  const file = new File(['report'], 'report.txt', { type: 'text/plain' })
  const input = document.querySelector('input[type="file"]')
  Object.defineProperty(input, 'files', { value: [file] })
  input.dispatchEvent(new Event('change', { bubbles: true }))
  expect(document.querySelector('.mail-signature img[src]')).toBeNull()
  button('Senden').click()
  await vi.waitFor(() => expect(api.post).toHaveBeenCalledOnce())
  const [path, data] = api.post.mock.calls[0]
  expect(path).toBe('/mail/accounts/work/messages/send')
  expect(data.getAll('cc')).toEqual(['cc@example.com'])
  expect(data.getAll('bcc')).toEqual(['bcc@example.com'])
  expect(data.get('attachments').name).toBe('report.txt')
})

it('sends an existing Graph draft unchanged with the displayed version', async () => {
  account.capabilities.send = true
  await mount()
  root.querySelector('.q-item--clickable').click()
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Entwurf unverändert senden"]')).toBeTruthy())
  root.querySelector('[aria-label="Entwurf unverändert senden"]').click()
  await vi.waitFor(() => expect(api.post).toHaveBeenCalledOnce())
  const [path, data] = api.post.mock.calls[0]
  expect(path).toBe('/mail/accounts/work/drafts/message/send')
  expect([...data.entries()]).toEqual([['expectedVersion', 'version']])
})

it.each([false, true])('saves an existing Microsoft draft before sending, preserving unchanged HTML: %s', async (editBody) => {
  Object.assign(account.capabilities, { editDraft: true, send: true, uploadAttachments: true, ccBcc: true })
  account.signatureHtml = '<b>Must not be appended again</b>'
  const html = '<table><tr><td>Original</td><td><img src="cid:logo"></td></tr></table>'
  const get = api.get.getMockImplementation()
  api.get.mockImplementation(async (path) => path.endsWith('/messages/message')
    ? { data: { message: {
      messageToken: 'message', subject: 'Subject', body: 'Original', editBody: html, editBodyFormat: 'html',
      isDraft: true, version: 'version', to: [{ name: 'Alice Example', address: 'to@example.com' }],
      attachments: [{ attachmentToken: 'logo', name: 'Logo', isInline: true }]
    } } }
    : get(path))
  api.put.mockResolvedValue({ data: { message: { messageToken: 'message', version: 'saved-version', isDraft: true } } })
  await mount()
  root.querySelector('.q-item--clickable').click()
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Entwurf bearbeiten"]')).toBeTruthy())
  root.querySelector('[aria-label="Entwurf bearbeiten"]').click()
  await vi.waitFor(() => expect(document.querySelector('textarea')?.value).toBe(html))
  expect(document.querySelector('.mail-signature')).toBeNull()
  expect(document.querySelector('[aria-label="Anhang aus Entwurf entfernen"]')).toBeNull()
  if (editBody) await fill('Nachricht (HTML)', html.replace('Original', 'Changed'))
  await fill('Betreff', 'Changed subject')
  button('Senden').click()
  await vi.waitFor(() => expect(api.post).toHaveBeenCalledOnce())
  const data = api.put.mock.calls[0][1]
  expect(data.getAll('to')).toEqual(['to@example.com'])
  expect(data.get('expectedVersion')).toBe('version')
  expect(data.get('preserveBody')).toBe(editBody ? null : 'true')
  expect(data.get('bodyFormat')).toBe('html')
  expect(data.getAll('retainedAttachmentToken')).toEqual(['logo'])
  expect([...api.post.mock.calls[0][1].entries()]).toEqual([['expectedVersion', 'saved-version']])
})

it.each(['integration.staleReference', 'integration.outcomeUnknown'])('retries unchanged drafts safely after %s', async (code) => {
  account.capabilities.send = true
  api.post.mockRejectedValueOnce({ response: { data: { error: { code } } } })
  await mount()
  root.querySelector('.q-item--clickable').click()
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Entwurf unverändert senden"]')).toBeTruthy())
  const send = root.querySelector('[aria-label="Entwurf unverändert senden"]')
  send.click()
  await vi.waitFor(() => expect(api.post).toHaveBeenCalledOnce())
  await vi.waitFor(() => expect(send.disabled).toBe(false))
  const get = api.get.getMockImplementation()
  api.get.mockImplementation(async (path) => {
    const response = await get(path)
    if (response.data.message) response.data.message.version = 'fresh-version'
    return response
  })
  root.querySelector('.q-item--clickable').click()
  await vi.waitFor(() => expect(useMailStore().detail?.version).toBe('fresh-version'))
  root.querySelector('[aria-label="Entwurf unverändert senden"]').click()
  await vi.waitFor(() => expect(api.post).toHaveBeenCalledTimes(2))
  expect(api.post.mock.calls[1][1].get('expectedVersion')).toBe(code === 'integration.staleReference' ? 'fresh-version' : 'version')
  expect(api.post.mock.calls[1][2].headers['Idempotency-Key'] === api.post.mock.calls[0][2].headers['Idempotency-Key']).toBe(code !== 'integration.staleReference')
})

it('does not send or resubmit after an uncertain draft edit', async () => {
  Object.assign(account.capabilities, { editDraft: true, send: true })
  api.put.mockRejectedValue({ response: { data: { error: { code: 'integration.outcomeUnknown' } } } })
  await mount()
  root.querySelector('.q-item--clickable').click()
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Entwurf bearbeiten"]')).toBeTruthy())
  root.querySelector('[aria-label="Entwurf bearbeiten"]').click()
  await vi.waitFor(() => expect(button('Senden')).toBeTruthy())
  await fill('An', 'to@example.com')
  button('Senden').click()
  await vi.waitFor(() => expect(button('Senden').disabled).toBe(true))
  expect(api.post).not.toHaveBeenCalled()
})
