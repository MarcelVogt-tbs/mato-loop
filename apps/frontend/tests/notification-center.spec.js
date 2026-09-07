// @vitest-environment jsdom
import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { Quasar, QAvatar, QBadge, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QExpansionItem, QIcon, QInnerLoading, QInput, QItem, QItemLabel, QItemSection, QList, QRadio, QScrollArea, QSelect, QSeparator, QSkeleton, QSpace, QToggle, QTooltip } from 'quasar'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn() } }))

import NotificationCenterApp from '../src/components/NotificationCenterApp.vue'
import { useApprovalStore } from '../src/stores/approvals'
import { useQuestionStore } from '../src/stores/questions'
import { useInAppNotificationStore } from '../src/stores/in-app-notifications'
import { api } from 'src/boot/axios'

const components = { QAvatar, QBadge, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QExpansionItem, QIcon, QInnerLoading, QInput, QItem, QItemLabel, QItemSection, QList, QRadio, QScrollArea, QSelect, QSeparator, QSkeleton, QSpace, QToggle, QTooltip }
const createdAt = '2026-09-06T10:00:00Z'
let app, root, pinia, responses

beforeEach(() => {
  vi.resetAllMocks()
  pinia = createPinia()
  setActivePinia(pinia)
  responses = {
    '/approvals/pending': [{
      id: 'approval', sessionId: 'parent', title: 'Datei lesen', createdAt,
      sourceSession: { title: 'Quellsession' }, riskLevel: 'low',
      resourceType: 'path', resourceScope: '/notes', availableDecisions: ['allow_once', 'deny']
    }],
    '/questions/pending': [{
      id: 'question', sessionId: 'parent', createdAt,
      questions: [{ id: 'answer', type: 'free_text', question: 'Welche Datei?' }]
    }],
    '/notifications': {
      items: [{ id: 'notice', title: 'Automation abgeschlossen', createdAt, readAt: null }],
      unreadCount: 1, nextCursor: null
    }
  }
  api.get.mockImplementation(async (url) => ({ data: responses[url] }))
  api.post.mockResolvedValue({ data: { unreadCount: 0 } })
})

afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.unstubAllGlobals()
})

async function mountCenter(dark = false) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', name: 'chat', component: { render: () => null } }]
  })
  await router.push('/')
  root = document.createElement('div')
  document.body.append(root)
  app = createApp(NotificationCenterApp)
  app.use(pinia).use(router).use(Quasar, { components, config: { dark } })
  app.config.globalProperties.$q.dark.set(dark)
  app.mount(root)
  await vi.waitFor(() => expect(api.get).toHaveBeenCalledTimes(3))
  await nextTick()
  return router
}

function button(container, label) {
  return [...container.querySelectorAll('button')].find((item) => item.textContent.includes(label))
}

describe('notification center', () => {
  it.each([false, true])('keeps required actions after read-all and removes only resolved requests (dark=%s)', async (dark) => {
    await mountCenter(dark)
    const approvals = useApprovalStore()
    const questions = useQuestionStore()
    const notifications = useInAppNotificationStore()
    const actions = root.querySelector('[aria-labelledby="notification-actions-title"]')
    expect(root.querySelectorAll('.q-scrollarea')).toHaveLength(1)
    expect(actions.querySelector('.approval-card')).not.toBeNull()
    expect(actions.querySelector('.question-dock')).not.toBeNull()
    expect(actions.querySelector('.q-badge').textContent).toBe('2')
    expect(root.textContent.indexOf('Datei lesen')).toBeLessThan(root.textContent.indexOf('Automation abgeschlossen'))
    expect(root.textContent.indexOf('Welche Datei?')).toBeLessThan(root.textContent.indexOf('Automation abgeschlossen'))
    expect(root.querySelector('[aria-label="Agent-Anfrage stoppen"]')).toBeNull()

    const readAll = root.querySelector('[aria-label="Alle Benachrichtigungen als gelesen markieren"]')
    readAll.click()
    await vi.waitFor(() => expect(notifications.unreadCount).toBe(0))
    expect(notifications.items[0].readAt).toBeTruthy()
    expect(readAll.disabled).toBe(true)
    expect(approvals.pending).toHaveLength(1)
    expect(questions.pending).toHaveLength(1)
    expect(actions.querySelector('.q-badge').textContent).toBe('2')
    expect(actions.querySelector('.approval-card')).not.toBeNull()
    expect(actions.querySelector('.question-dock')).not.toBeNull()
    expect(api.post).toHaveBeenCalledExactlyOnceWith('/notifications/read-all')

    button(actions.querySelector('.approval-card'), 'Einmal erlauben').click()
    await vi.waitFor(() => expect(actions.querySelector('.approval-card')).toBeNull())
    expect(api.post).toHaveBeenCalledWith('/approvals/approval/decisions', { decision: 'allow_once' })
    expect(actions.querySelector('.question-dock')).not.toBeNull()
    const input = actions.querySelector('textarea')
    input.value = 'notes.md'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    button(actions.querySelector('.question-dock'), 'Antworten senden').click()
    await vi.waitFor(() => expect(actions.querySelector('.question-dock')).toBeNull())
    expect(api.post).toHaveBeenCalledWith('/questions/question/answers', { answers: { answer: ['notes.md'] } })
    expect(actions.querySelector('.q-badge')).toBeNull()
    expect(root.textContent).toContain('Automation abgeschlossen')
  })

  it('retains type filters and session source links', async () => {
    responses['/approvals/pending'].push({
      id: 'memory', requestType: 'memory_change', actionKind: 'memory_pin', createdAt,
      availableDecisions: ['deny'], payload: {}
    })
    const router = await mountCenter()
    expect(root.querySelectorAll('.approval-card')).toHaveLength(2)
    expect(root.textContent).toContain('Dreaming im Hintergrund')
    const source = [...root.querySelectorAll('a')].find((link) => link.textContent.includes('Quellsession'))
    source.click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.session).toBe('parent'))

    root.querySelector('[role="combobox"]').click()
    await vi.waitFor(() => expect(document.querySelector('[role="option"]')).not.toBeNull())
    const option = [...document.querySelectorAll('[role="option"]')].find((item) => item.textContent.includes('Agent-Aktionen'))
    option.click()
    await vi.waitFor(() => expect(root.querySelectorAll('.approval-card')).toHaveLength(1))
    expect(root.querySelector('.approval-card').textContent).toContain('Datei lesen')
    expect(root.querySelector('.question-dock')).not.toBeNull()
    expect(root.textContent).toContain('Automation abgeschlossen')
    expect(useApprovalStore().pending).toHaveLength(2)
  })

  it.each(['/approvals/pending', '/questions/pending', '/notifications'])('refreshes healthy feeds when %s fails and allows retry', async (failedUrl) => {
    api.get.mockImplementation(async (url) => {
      if (url === failedUrl) throw new Error('Unavailable')
      return { data: responses[url] }
    })
    await mountCenter()
    expect(useApprovalStore().loaded).toBe(failedUrl !== '/approvals/pending')
    expect(useQuestionStore().loaded).toBe(failedUrl !== '/questions/pending')
    expect(useInAppNotificationStore().loaded).toBe(failedUrl !== '/notifications')
    if (failedUrl !== '/approvals/pending') expect(root.querySelector('.approval-card')).not.toBeNull()
    if (failedUrl !== '/questions/pending') expect(root.querySelector('.question-dock')).not.toBeNull()
    if (failedUrl !== '/notifications') expect(root.textContent).toContain('Automation abgeschlossen')
    expect(root.querySelector('.q-banner').textContent).toContain('konnten nicht')

    api.get.mockImplementation(async (url) => ({ data: responses[url] }))
    button(root, 'Erneut laden').click()
    await vi.waitFor(() => expect(api.get).toHaveBeenCalledTimes(6))
    await nextTick()
    expect(root.querySelector('.q-banner')).toBeNull()
    expect(root.querySelector('.approval-card')).not.toBeNull()
    expect(root.querySelector('.question-dock')).not.toBeNull()
    expect(root.textContent).toContain('Automation abgeschlossen')
  })
})
