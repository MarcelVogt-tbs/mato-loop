// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { Quasar, QAvatar, QBadge, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QDialog, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel, QItemSection, QList, QPagination, QRadio, QScrollArea, QSelect, QSeparator, QSkeleton, QSpace, QTab, QTabPanel, QTabPanels, QTabs, QToggle, QTooltip } from 'quasar'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm }) }))

import NotificationCenterApp from '../src/features/notifications/NotificationCenterApp.vue'
import ApprovalCard from '../src/features/approvals/ApprovalCard.vue'
import MemorySettingsPage from '../src/features/memory/MemorySettingsPage.vue'
import { useApprovalStore } from '../src/stores/approvals'
import { useInAppNotificationStore } from '../src/stores/in-app-notifications'
import { api } from 'src/boot/axios'

const { confirm } = vi.hoisted(() => ({ confirm: vi.fn() }))
const components = { QAvatar, QBadge, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QDialog, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel, QItemSection, QList, QPagination, QRadio, QScrollArea, QSelect, QSeparator, QSkeleton, QSpace, QTab, QTabPanel, QTabPanels, QTabs, QToggle, QTooltip }
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
  delete Element.prototype.scrollIntoView
})

async function mountCenter(dark = false, approvalOnly = false) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'chat', component: { render: () => null } },
      { path: '/settings/memory', name: 'settings-memory', component: { render: () => null } }
    ]
  })
  await router.push('/')
  root = document.createElement('div')
  document.body.append(root)
  app = createApp(approvalOnly ? {
    setup() {
      const store = useApprovalStore()
      store.pending = responses['/approvals/pending']
      return () => store.pending.map((approval) => h(ApprovalCard, { approval, showDraftAction: false }))
    }
  } : NotificationCenterApp)
  app.use(pinia).use(router).use(Quasar, { components, config: { dark } })
  app.config.globalProperties.$q.dark.set(dark)
  app.mount(root)
  if (!approvalOnly) await vi.waitFor(() => expect(api.get).toHaveBeenCalledTimes(1))
  await nextTick()
  if (!approvalOnly) await vi.waitFor(() => expect(root.querySelector('[aria-label="Benachrichtigungen aktualisieren"] .q-spinner')).toBeNull())
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
  it('deletes a notice directly without confirmation, navigation or marking it read', async () => {
    const router = await mountCenter()
    api.delete.mockResolvedValue({ data: { unreadCount: 0 } })
    root.querySelector('[aria-label="Benachrichtigung löschen"]').click()
    await vi.waitFor(() => expect(useInAppNotificationStore().items).toHaveLength(0))
    expect(confirm).not.toHaveBeenCalled()
    expect(api.post).not.toHaveBeenCalled()
    expect(router.currentRoute.value.query).toEqual({})
    useInAppNotificationStore().loading = true
    await nextTick()
    expect([root.textContent.includes('Keine Benachrichtigungen'), root.querySelector('[aria-label="Benachrichtigungen aktualisieren"] .q-spinner')]).toEqual([true, null])
  })

  it('submits administrator session approval without collecting a password', async () => {
    responses['/approvals/pending'][0] = {
      ...responses['/approvals/pending'][0], requestType: 'agent_action', resourceType: 'execution_administrator',
      capability: 'filesystem.write', availableDecisions: ['allow_once', 'allow_session', 'deny'],
      payload: {}
    }
    await mountCenter(false, true)
    const card = root.querySelector('.approval-card')
    expect(card.querySelector('input[type="password"]')).toBeNull()
    card.querySelector('.q-checkbox').click()
    await nextTick()
    button(card, 'Für Session erlauben').click()
    await vi.waitFor(() => expect(api.post).toHaveBeenCalledExactlyOnceWith('/approvals/approval/decisions', {
      decision: 'allow_session'
    }))
  })

  it.each([false, true])('renders every notification as an ordinary notice and acknowledges them together (dark=%s)', async (dark) => {
    responses['/notifications'] = {
      items: ['question.requested', 'approval.requested', 'memory.approval', 'session.response'].map((kind) => ({
        id: kind, kind, title: kind, createdAt, readAt: null
      })), unreadCount: 4, nextCursor: null
    }
    await mountCenter(dark)
    const notifications = useInAppNotificationStore()
    const notices = root.querySelector('[aria-label="Benachrichtigungen"]')
    expect([...notices.children].map((item) => item.classList.contains('q-item'))).toEqual([true, true, true, true])
    expect(notices.querySelector('.approval-card, .question-dock, .q-expansion-item')).toBeNull()
    const readAll = root.querySelector('[aria-label="Alle Benachrichtigungen als gelesen markieren"]')
    responses['/notifications'] = {
      items: responses['/notifications'].items.filter((item) => item.kind !== 'session.response')
        .map((item) => ({ ...item, readAt: createdAt })), unreadCount: 0, nextCursor: null
    }
    readAll.click()
    await vi.waitFor(() => expect(notifications.unreadCount).toBe(0))
    await vi.waitFor(() => expect(notifications.items).toHaveLength(3))
    expect(notifications.items.every((item) => item.readAt)).toBe(true)
    expect(api.post).toHaveBeenCalledExactlyOnceWith('/notifications/read-all')
  })

  it.each([
    [{ type: 'session', sessionId: 'parent' }, 'chat', { session: 'parent' }],
    [{ type: 'memoryApproval', approvalId: 'memory' }, 'settings-memory', { tab: 'dreaming', approval: 'memory' }]
  ])('opens the target and marks the notice read (%j)', async (target, name, query) => {
    const notice = responses['/notifications'].items[0]
    notice.target = target
    api.post.mockResolvedValue({ data: { notification: { ...notice, readAt: createdAt }, unreadCount: 0 } })
    const router = await mountCenter()
    root.querySelector('[aria-label="Benachrichtigungen"] > .q-item').click()
    await vi.waitFor(() => expect(useInAppNotificationStore().unreadCount).toBe(0))
    expect([router.currentRoute.value.name, router.currentRoute.value.query]).toEqual([name, query])
    expect(api.post).toHaveBeenCalledExactlyOnceWith('/notifications/notice/read')
  })

  it('allows retry when notifications fail to load', async () => {
    api.get.mockRejectedValueOnce(new Error('Unavailable'))
    await mountCenter()
    expect(root.querySelector('.q-banner').textContent).toContain('konnten nicht')
    button(root, 'Erneut laden').click()
    await vi.waitFor(() => expect(api.get).toHaveBeenCalledTimes(2))
    await nextTick()
    expect(root.querySelector('.q-banner')).toBeNull()
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
    await mountCenter(dark, true)
    const card = root.querySelector('.approval-card')
    card.querySelector('[aria-label="Vorschlag bearbeiten"]').click()
    await nextTick()
    expect(card.querySelector('form')).not.toBeNull()
    return card
  }

  it('opens the linked proposal in Dreaming and allows its decision there', async () => {
    Element.prototype.scrollIntoView = vi.fn()
    responses['/memories'] = { items: [], total: 0, offset: 0, limit: 20 }
    responses['/memories/settings'] = {}
    responses['/memories/dream-runs'] = { items: [] }
    responses['/sessions'] = { items: [], nextCursor: null }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/settings/memory', name: 'settings-memory', component: { render: () => null } }]
    })
    await router.push('/settings/memory?tab=dreaming&approval=memory')
    root = document.createElement('div')
    document.body.append(root)
    app = createApp(MemorySettingsPage)
    app.use(pinia).use(router).use(Quasar, { components })
    app.mount(root)
    await vi.waitFor(() => expect(document.activeElement?.id).toBe('memory-proposal-memory'))
    const card = root.querySelector('.approval-card')
    card.querySelector('[aria-label="Vorschlag bearbeiten"]').click()
    await nextTick()
    const input = card.querySelector('textarea')
    input.focus()
    responses['/approvals/pending'] = [structuredClone(approval)]
    await useApprovalStore().fetchPending()
    await nextTick()
    expect(document.activeElement).toBe(input)
    button(card, 'Bearbeitung verwerfen').click()
    await nextTick()
    button(root.querySelector('.approval-card'), 'Genehmigen').click()
    await vi.waitFor(() => expect(root.querySelector('.approval-card')).toBeNull())
    expect(api.post).toHaveBeenCalledExactlyOnceWith('/approvals/memory/decisions', { decision: 'approve' })
  })

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
    await mountCenter(false, true)
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
