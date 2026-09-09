// @vitest-environment jsdom
import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { Quasar, QAvatar, QBadge, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel, QItemSection, QList, QRadio, QScrollArea, QSelect, QSeparator, QSkeleton, QSpace, QToggle, QTooltip } from 'quasar'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm }) }))

import NotificationCenterApp from '../src/components/NotificationCenterApp.vue'
import { useApprovalStore } from '../src/stores/approvals'
import { useQuestionStore } from '../src/stores/questions'
import { useInAppNotificationStore } from '../src/stores/in-app-notifications'
import { api } from 'src/boot/axios'

const { confirm } = vi.hoisted(() => ({ confirm: vi.fn() }))
const components = { QAvatar, QBadge, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel, QItemSection, QList, QRadio, QScrollArea, QSelect, QSeparator, QSkeleton, QSpace, QToggle, QTooltip }
const createdAt = '2026-09-06T10:00:00Z'
let app, root, pinia, responses

beforeEach(() => {
  vi.resetAllMocks()
  confirm.mockResolvedValue(true)
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

async function setContent(card, value) {
  const input = card.querySelector('textarea')
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
}

async function selectOption(card, label, option) {
  const field = [...card.querySelectorAll('label')].find((item) => item.textContent.includes(label))
  field.querySelector('[role="combobox"]').click()
  await vi.waitFor(() => expect(document.querySelector('[role="option"]')).not.toBeNull())
  const choice = [...document.querySelectorAll('[role="option"]')].find((item) => item.textContent === option)
  choice.click()
  await vi.waitFor(() => expect(field.textContent).toContain(option))
  await vi.waitFor(() => expect(document.querySelector('[role="option"]')).toBeNull())
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

describe('inline memory approval edits', () => {
  let approval

  beforeEach(() => {
    approval = {
      id: 'memory', requestType: 'memory_change', actionKind: 'memory_update', status: 'pending', createdAt,
      availableDecisions: ['approve', 'deny'],
      payload: {
        targetMemories: [{ id: 'original', content: 'Original memory', category: 'context', audience: 'private_only', pinned: false }],
        after: { content: 'Proposed memory', category: 'fact', audience: 'private_only' }
      }
    }
    responses['/approvals/pending'] = [approval]
  })

  async function openEdit(dark = false) {
    await mountCenter(dark)
    const card = root.querySelector('.approval-card')
    card.querySelector('[aria-label="Vorschlag bearbeiten"]').click()
    await nextTick()
    expect(card.querySelector('form')).not.toBeNull()
    return card
  }

  it.each([
    ['pin', { actionKind: 'memory_pin' }],
    ['remove', { actionKind: 'memory_remove' }],
    ['unknown action', { actionKind: 'memory_other' }],
    ['resolved', { status: 'approved' }],
    ['agent action', { requestType: 'agent_action' }],
    ['deny only', { availableDecisions: ['deny'] }],
    ['missing targets', { payload: { after: { content: 'Incomplete' } } }],
    ['missing result', { payload: { targetMemories: [{ content: 'Original' }] } }]
  ])('does not offer editing for %s', async (_, overrides) => {
    Object.assign(approval, overrides)
    await mountCenter()
    expect(root.querySelector('[aria-label="Vorschlag bearbeiten"]')).toBeNull()
    expect(root.querySelector('.approval-card form')).toBeNull()
    expect(api.post).not.toHaveBeenCalled()
  })

  it.each([false, true])('submits all four fields only on approval and retains them after failure (dark=%s)', async (dark) => {
    const card = await openEdit(dark)
    const originalPayload = structuredClone(approval.payload)
    await setContent(card, '  Edited memory  ')
    await selectOption(card, 'Kategorie', 'Ziel')
    await selectOption(card, 'Verfügbar für', 'Öffentliche und private Modelle')
    card.querySelector('[role="switch"]').click()
    await nextTick()
    expect(api.post).not.toHaveBeenCalled()
    expect(confirm).not.toHaveBeenCalled()
    expect(approval.payload).toEqual(originalPayload)

    api.post.mockRejectedValueOnce(new Error('Unavailable'))
    button(card, 'Speichern und genehmigen').click()
    const memoryEdit = { content: 'Edited memory', category: 'goal', audience: 'both', pinned: true }
    await vi.waitFor(() => expect(api.post).toHaveBeenCalledExactlyOnceWith('/approvals/memory/decisions', {
      decision: 'approve', memory_edit: memoryEdit
    }))
    await vi.waitFor(() => expect(button(card, 'Speichern und genehmigen').disabled).toBe(false))
    expect(confirm.mock.calls.map((call) => call[1])).toEqual(['Für öffentliche Modelle freigeben', 'Immer im Kontext'])
    expect(useApprovalStore().pending).toHaveLength(1)
    expect(useApprovalStore().submittingId).toBeNull()
    expect(card.querySelector('textarea').value).toBe('  Edited memory  ')
    expect(card.querySelector('form').textContent).toContain('Ziel')
    expect(card.querySelector('form').textContent).toContain('Öffentliche und private Modelle')
    expect(card.querySelector('[role="switch"]').getAttribute('aria-checked')).toBe('true')

    button(card, 'Speichern und genehmigen').click()
    await vi.waitFor(() => expect(root.querySelector('.approval-card')).toBeNull())
    expect(api.post).toHaveBeenCalledTimes(2)
    expect(api.post).toHaveBeenLastCalledWith('/approvals/memory/decisions', { decision: 'approve', memory_edit: memoryEdit })
  })

  it('discards local edits and approves the original proposal without an edit payload', async () => {
    const card = await openEdit()
    await setContent(card, 'Discarded memory')
    await selectOption(card, 'Kategorie', 'Ziel')
    await selectOption(card, 'Verfügbar für', 'Nur öffentliche Modelle')
    card.querySelector('[role="switch"]').click()
    await nextTick()
    button(card, 'Bearbeitung verwerfen').click()
    await nextTick()
    expect(card.querySelector('form')).toBeNull()
    expect(card.textContent).toContain('Proposed memory')
    expect(card.textContent).not.toContain('Discarded memory')
    expect(api.post).not.toHaveBeenCalled()
    card.querySelector('[aria-label="Vorschlag bearbeiten"]').click()
    await nextTick()
    expect(card.querySelector('textarea').value).toBe('Proposed memory')
    expect(card.querySelector('form').textContent).toContain('Fakt')
    expect(card.querySelector('form').textContent).toContain('Nur private Modelle')
    expect(card.querySelector('[role="switch"]').getAttribute('aria-checked')).toBe('false')
    button(card, 'Bearbeitung verwerfen').click()
    await nextTick()
    button(card, 'Genehmigen').click()
    await vi.waitFor(() => expect(root.querySelector('.approval-card')).toBeNull())
    expect(api.post).toHaveBeenCalledExactlyOnceWith('/approvals/memory/decisions', { decision: 'approve' })
    expect(confirm).not.toHaveBeenCalled()
  })

  it('blocks blank approval but allows denial without validating or submitting edits', async () => {
    const card = await openEdit()
    await setContent(card, '   ')
    await selectOption(card, 'Verfügbar für', 'Nur öffentliche Modelle')
    card.querySelector('[role="switch"]').click()
    await nextTick()
    button(card, 'Speichern und genehmigen').click()
    await vi.waitFor(() => expect(card.textContent).toContain('Bitte einen Inhalt eingeben.'))
    expect(api.post).not.toHaveBeenCalled()
    expect(confirm).not.toHaveBeenCalled()
    expect(card.querySelector('textarea').value).toBe('   ')
    button(card, 'Ablehnen').click()
    await vi.waitFor(() => expect(root.querySelector('.approval-card')).toBeNull())
    expect(api.post).toHaveBeenCalledExactlyOnceWith('/approvals/memory/decisions', { decision: 'deny' })
    expect(confirm).not.toHaveBeenCalled()
  })

  it.each([
    ['public access', 'Nur öffentliche Modelle', false, 1],
    ['pinning', null, true, 1],
    ['pinning after public access', 'Öffentliche und private Modelle', true, 2]
  ])('keeps edits without posting when %s confirmation is cancelled', async (_, audience, pinned, count) => {
    const card = await openEdit()
    await setContent(card, 'Keep this edit')
    if (audience) await selectOption(card, 'Verfügbar für', audience)
    if (pinned) card.querySelector('[role="switch"]').click()
    await nextTick()
    expect(confirm).not.toHaveBeenCalled()
    expect(api.post).not.toHaveBeenCalled()
    if (count === 2) confirm.mockResolvedValueOnce(true)
    let resolveConfirmation
    confirm.mockImplementationOnce(() => new Promise((resolve) => { resolveConfirmation = resolve }))
    button(card, 'Speichern und genehmigen').click()
    await vi.waitFor(() => expect(confirm).toHaveBeenCalledTimes(count))
    expect(api.post).not.toHaveBeenCalled()
    resolveConfirmation(false)
    await vi.waitFor(() => expect(button(card, 'Speichern und genehmigen').disabled).toBe(false))
    expect(api.post).not.toHaveBeenCalled()
    expect(useApprovalStore().pending).toHaveLength(1)
    expect(card.querySelector('textarea').value).toBe('Keep this edit')
    if (audience) expect(card.querySelector('form').textContent).toContain(audience)
    expect(card.querySelector('[role="switch"]').getAttribute('aria-checked')).toBe(String(pinned))
  })

  it.each([
    ['private_only', 'Nur öffentliche Modelle', 'public_only', 1],
    ['private_only', 'Öffentliche und private Modelle', 'both', 1],
    ['public_only', 'Nur private Modelle', 'private_only', 0],
    ['both', 'Nur private Modelle', 'private_only', 0],
    ['public_only', 'Öffentliche und private Modelle', 'both', 0]
  ])('confirms only widening private-only access (%s to %s)', async (initial, label, audience, confirmations) => {
    approval.payload.after.audience = initial
    const card = await openEdit()
    await selectOption(card, 'Verfügbar für', label)
    expect(confirm).not.toHaveBeenCalled()
    expect(api.post).not.toHaveBeenCalled()
    button(card, 'Speichern und genehmigen').click()
    await vi.waitFor(() => expect(root.querySelector('.approval-card')).toBeNull())
    expect(confirm).toHaveBeenCalledTimes(confirmations)
    expect(api.post).toHaveBeenCalledExactlyOnceWith('/approvals/memory/decisions', {
      decision: 'approve', memory_edit: { ...approval.payload.after, audience, pinned: false }
    })
  })

  it.each([
    ['memory_update', [false], false],
    ['memory_update', [true], true],
    ['memory_merge', [false, false], false],
    ['memory_merge', [false, true], true]
  ])('preserves initial pin state for %s targets %j', async (actionKind, pins, pinned) => {
    approval.actionKind = actionKind
    approval.payload.targetMemories = pins.map((value, index) => ({
      ...approval.payload.targetMemories[0], id: `target-${index}`, pinned: value
    }))
    const card = await openEdit()
    expect(card.querySelector('textarea').value).toBe('Proposed memory')
    expect(card.querySelector('[role="switch"]').getAttribute('aria-checked')).toBe(String(pinned))
    button(card, 'Speichern und genehmigen').click()
    await vi.waitFor(() => expect(root.querySelector('.approval-card')).toBeNull())
    expect(api.post).toHaveBeenCalledExactlyOnceWith('/approvals/memory/decisions', {
      decision: 'approve', memory_edit: { ...approval.payload.after, pinned }
    })
    expect(confirm).not.toHaveBeenCalled()
  })
})
