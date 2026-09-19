// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import {
  Quasar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QIcon,
  QInput, QRadio, QSeparator, QSpace, QTooltip
} from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('components/SubagentRequestLink.vue', () => ({ default: { render: () => null } }))

const { api } = vi.hoisted(() => ({ api: { post: vi.fn() } }))
vi.mock('src/boot/axios', () => ({ api }))

import QuestionDock from '../src/components/QuestionDock.vue'

let app
let root

afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.clearAllMocks()
})

it('masks password questions, submits exact values, and clears failed drafts', async () => {
  api.post.mockRejectedValue(new Error('network'))
  const request = {
    id: 'question-1',
    questions: [{
      id: 'token',
      header: 'Token',
      question: 'Wie lautet der Token?',
      type: 'password',
      save_proposal: { name: 'GITHUB_TOKEN' }
    }]
  }
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(QuestionDock, { request, showStop: false }) })
  app.use(createPinia()).use(Quasar, { components: {
    QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QIcon,
    QInput, QRadio, QSeparator, QSpace, QTooltip
  } })
  app.mount(root)
  await nextTick()

  expect(root.textContent).toContain('Secret „GITHUB_TOKEN“')
  expect(root.textContent).toContain('Erst „Antworten senden“ erlaubt genau diesen Speichervorgang.')
  const input = root.querySelector('input[type="password"]')
  expect(input.autocomplete).toBe('new-password')
  input.value = '  exact secret  '
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
  ;[...root.querySelectorAll('button')].find((button) => button.textContent.includes('Antworten senden')).click()

  await vi.waitFor(() => expect(api.post).toHaveBeenCalledWith('/questions/question-1/answers', {
    answers: { token: ['  exact secret  '] }
  }))
  await vi.waitFor(() => expect(input.value).toBe(''))

  input.value = 'retry secret'
  input.dispatchEvent(new Event('input', { bubbles: true }))
  ;[...root.querySelectorAll('button')].find((button) => button.textContent.includes('Ablehnen')).click()
  await vi.waitFor(() => expect(api.post).toHaveBeenCalledWith('/questions/question-1/reject'))
  await vi.waitFor(() => expect(input.value).toBe(''))
})

it('submits visible and masked one-time credentials together and clears both', async () => {
  api.post.mockRejectedValue(new Error('network'))
  const request = {
    id: 'question-1',
    questions: [
      {
        id: 'SSH_USERNAME',
        header: 'Benutzername',
        question: 'Wie lautet der Benutzername?',
        type: 'value',
        one_time: true
      },
      {
        id: 'SSH_PASSWORD',
        header: 'Passwort',
        question: 'Wie lautet das Passwort?',
        type: 'password',
        one_time: true
      }
    ]
  }
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(QuestionDock, { request, showStop: false }) })
  app.use(createPinia()).use(Quasar, { components: {
    QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QIcon,
    QInput, QRadio, QSeparator, QSpace, QTooltip
  } })
  app.mount(root)
  await nextTick()

  expect(root.textContent).toContain('Diese Antwort wird nicht gespeichert.')
  expect(root.textContent).not.toContain('dauerhaft')
  const username = root.querySelector('input[type="text"]')
  expect(username.autocomplete).toBe('off')
  username.value = '  viktor  '
  username.dispatchEvent(new Event('input', { bubbles: true }))
  ;[...root.querySelectorAll('button')].find((button) => button.textContent.includes('Weiter')).click()
  await nextTick()

  const password = root.querySelector('input[type="password"]')
  expect(password.autocomplete).toBe('new-password')
  password.value = '  exact secret  '
  password.dispatchEvent(new Event('input', { bubbles: true }))
  ;[...root.querySelectorAll('button')].find((button) => button.textContent.includes('Antworten senden')).click()

  await vi.waitFor(() => expect(api.post).toHaveBeenCalledWith('/questions/question-1/answers', {
    answers: {
      SSH_USERNAME: ['  viktor  '],
      SSH_PASSWORD: ['  exact secret  ']
    }
  }))
  await vi.waitFor(() => expect(password.value).toBe(''))
  ;[...root.querySelectorAll('button')].find((button) => button.textContent.includes('Zurück')).click()
  await nextTick()
  expect(root.querySelector('input[type="text"]').value).toBe('')
})
