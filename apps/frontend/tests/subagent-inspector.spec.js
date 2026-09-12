// @vitest-environment jsdom
import { computed, createApp, defineComponent, h, nextTick, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { Quasar, QAvatar, QBanner, QBreadcrumbs, QBreadcrumbsEl, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QDialog, QExpansionItem, QForm, QIcon, QInput, QItem, QItemLabel, QItemSection, QList, QRadio, QScrollArea, QSelect, QSeparator, QSpace, QSpinner, QToggle, QTooltip } from 'quasar'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } }))
vi.mock('components/MessageContent.vue', () => ({ default: defineComponent({ props: ['content'], setup: (props) => () => h('div', props.content) }) }))
vi.mock('components/MessageAttachments.vue', () => ({ default: { render: () => null } }))

import SubagentInspector from '../src/components/SubagentInspector.vue'
import SubagentRows from '../src/components/SubagentRows.vue'
import AgentActivityPanel from '../src/components/AgentActivityPanel.vue'
import ApprovalCard from '../src/components/ApprovalCard.vue'
import QuestionDock from '../src/components/QuestionDock.vue'
import SubagentRequestLink from '../src/components/SubagentRequestLink.vue'
import { useSubagentStore } from '../src/stores/subagents'
import { useApprovalStore } from '../src/stores/approvals'
import { useQuestionStore } from '../src/stores/questions'
import { useChatStore } from '../src/stores/chat'
import { useAgentProfileStore } from '../src/stores/agent-profiles'
import { buildChatTranscript } from '../src/utils/chat-transcript'
import { agentRunPresentation } from '../src/utils/agent-activity'
import { api } from 'src/boot/axios'

const components = { QAvatar, QBanner, QBreadcrumbs, QBreadcrumbsEl, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QDialog, QExpansionItem, QForm, QIcon, QInput, QItem, QItemLabel, QItemSection, QList, QRadio, QScrollArea, QSelect, QSeparator, QSpace, QSpinner, QToggle, QTooltip }
let app, root, store
afterEach(() => {
  app?.unmount()
  root?.remove()
  store?.close()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('subagent inspector navigation and presentation', () => {
  it.each([false, true])('shows status indicators without removing readable labels (reduced motion=%s)', async (reducedMotion) => {
    vi.stubGlobal('matchMedia', () => ({ matches: reducedMotion }))
    const states = [
      ['queued', 'schedule', 'Wartet'], ['running', 'play_circle_outline', 'Arbeitet'],
      ['waiting_approval', 'pending_actions', 'Freigabe erforderlich'],
      ['waiting_input', 'help_outline', 'Antwort erforderlich'],
      ['completed', 'check_circle', 'Abgeschlossen'], ['failed', 'error_outline', 'Fehlgeschlagen'],
      ['cancelled', 'cancel', 'Abgebrochen']
    ]
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'chat', component: { render: () => null } }] })
    await router.push('/?session=parent')
    root = document.createElement('div')
    document.body.append(root)
    app = createApp({ render: () => h(SubagentRows, { children: states.map(([status]) => ({ id: status, title: `Auftrag ${status}`, parentSessionId: 'parent', run: { status } })) }) })
    app.use(router).use(Quasar, { components })
    app.mount(root)
    const rows = [...root.querySelectorAll('a')]
    for (const [index, [status, icon, label]] of states.entries()) {
      expect(rows[index].textContent).toContain(`Auftrag ${status}`)
      expect(rows[index].textContent).toContain(label)
      const animated = !reducedMotion && ['queued', 'running'].includes(status)
      expect(Boolean(rows[index].querySelector('.q-spinner'))).toBe(animated)
      if (!animated) expect(rows[index].querySelector('.q-icon').textContent).toBe(icon)
    }
    expect(root.querySelectorAll('.q-spinner')).toHaveLength(reducedMotion ? 0 : 2)
  })

  it.each([[1280, false], [1280, true], [390, false], [390, true]])('renders a passive Quasar inspector and returns to the mounted parent (width=%s, dark=%s)', async (width, dark) => {
    vi.stubGlobal('scrollTo', vi.fn())
    vi.stubGlobal('innerWidth', width)
    vi.stubGlobal('EventSource', class { addEventListener() {} close() {} })
    const pinia = createPinia()
    setActivePinia(pinia)
    useAgentProfileStore().agents = [{ id: 'agent', answerDisplay: 'timeline' }]
    store = useSubagentStore()
    const child = { id: 'child', parentSessionId: 'parent', title: 'Dateien untersuchen', run: { id: 'run', status: 'waiting_approval' } }
    api.get.mockImplementation(async (url) => ({ data: url.endsWith('/children') ? [child]
      : url.includes('/runs/') ? child.run
        : url.endsWith('/messages') ? { items: [{ id: 'old', role: 'user', content: 'Alter Auftrag' }], nextCursor: null }
          : { ...child, readOnly: true, nextMessageCursor: 'older', messages: [
            { id: 'task', role: 'user', content: 'Auftrag lesen' },
            { id: 'note', role: 'assistant', agentProfileId: 'agent', content: 'Ich prüfe die Datei.', toolCalls: [] },
            { id: 'tool', role: 'assistant', agentProfileId: 'agent', toolCalls: [{ id: 'read', name: 'read', arguments: {} }] },
            { id: 'answer', role: 'assistant', agentProfileId: 'agent', content: 'Ergebnis lesen' }
          ] } }))
    await store.loadChildren('parent')
    const request = { id: 'request', sessionId: 'parent', runId: 'run', title: 'Datei freigeben', details: { toolCallId: 'read' }, availableDecisions: ['allow_once', 'deny'], payload: { subagentSessionId: 'child', subagentTitle: child.title } }
    useApprovalStore().add(request)
    useQuestionStore().add({ ...request, id: 'question', questions: [{ id: 'q', question: 'Welche Datei?' }] })
    const Parent = defineComponent({
      setup: () => {
        const route = router.currentRoute
        return () => h('main', [
          h('textarea', { 'aria-label': 'Elternentwurf', value: 'Mein Entwurf' }),
          h('div', { id: 'parent-scroll', style: 'overflow: auto' }, 'Elternverlauf'),
          h(AgentActivityPanel, { history: { entries: [] }, subagents: [child] }),
          h(SubagentRequestLink, { request }),
          route.value.query.subagent ? h(SubagentInspector, { parentId: 'parent', childId: route.value.query.subagent }) : null
        ])
      }
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'chat', component: Parent }] })
    await router.push('/?session=parent&project=project')
    root = document.createElement('div')
    document.body.append(root)
    app = createApp({ render: () => h(RouterView) })
    app.use(pinia)
    app.use(router)
    app.use(Quasar, { config: { dark }, components })
    app.config.globalProperties.$q.dark.set(dark)
    app.mount(root)
    const parentScroll = document.getElementById('parent-scroll')
    parentScroll.scrollTop = 123
    const row = [...root.querySelectorAll('a')].find((link) => link.textContent.includes('Freigabe erforderlich'))
    expect(row.textContent).toContain(child.title)
    expect(row.getAttribute('href')).toContain('session=parent')
    expect(row.getAttribute('href')).toContain('subagent=child')
    row.focus()
    row.click()
    await vi.waitFor(() => expect(store.current?.id).toBe('child'))
    await nextTick()
    const dialog = document.querySelector('[role="dialog"]')
    expect(dialog).not.toBeNull()
    expect(dialog.textContent).toContain('Nur Lesezugriff')
    expect(dialog.textContent).toContain('Ergebnis lesen')
    expect(dialog.textContent).toContain('Ich prüfe die Datei.')
    expect(dialog.textContent).toContain('Datei freigeben')
    expect(dialog.textContent).toContain('Welche Datei?')
    expect(dialog.querySelector('.work-history')).not.toBeNull()
    expect(dialog.querySelector('.work-history__panel')).toBeNull()
    expect(dialog.querySelector('.approval-card, .question-dock')).toBeNull()
    expect(dialog.querySelector('textarea, input, [contenteditable="true"]')).toBeNull()
    expect(dialog.textContent).not.toMatch(/Einmal erlauben|Antworten senden|Ablehnen|Erneut versuchen|Einstellungen/)
    const olderButton = [...dialog.querySelectorAll('button')].find((button) => button.textContent.includes('Ältere Nachrichten laden'))
    olderButton.click()
    await vi.waitFor(() => expect(dialog.textContent).toContain('Alter Auftrag'))
    expect(api.post).not.toHaveBeenCalled()
    expect(api.patch).not.toHaveBeenCalled()
    expect(api.delete).not.toHaveBeenCalled()
    dialog.querySelector('a').click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.subagent).toBeUndefined())
    expect(router.currentRoute.value.query).toEqual({ session: 'parent', project: 'project' })
    expect(document.getElementById('parent-scroll')).toBe(parentScroll)
    expect(parentScroll.scrollTop).toBe(123)
    expect(root.querySelector('textarea').value).toBe('Mein Entwurf')
    expect(document.activeElement).toBe(row)
    expect(store.current).toBeNull()
    expect(api.get.mock.calls.filter(([url]) => url.endsWith('/children'))).toHaveLength(1)
  })

  it('keeps compact history open when a run completes', async () => {
    const pinia = createPinia()
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }] })
    await router.push('/')
    const running = ref(true)
    root = document.createElement('div')
    document.body.append(root)
    app = createApp({
      setup: () => () => h(AgentActivityPanel, {
        history: {
          entries: [{
            key: 'commentary', type: 'commentary',
            message: { content: 'Bisheriger Kommentar' }
          }]
        },
        running: running.value,
        activity: { phase: 'streaming' },
        presentation: { label: 'Antwort wird erstellt' },
        warnings: [{ code: 'privacy.placeholderRestorationIncomplete', modelRequest: 1 }]
      })
    })
    app.use(pinia).use(router).use(Quasar, { components })
    app.mount(root)

    const historyHeader = root.querySelector('.work-history__panel .q-item')
    expect(root.textContent).not.toContain('Bisheriger Kommentar')
    historyHeader.click()
    await nextTick()
    expect(root.textContent).toContain('Bisheriger Kommentar')
    running.value = false
    await nextTick()
    expect(root.textContent).toContain('Bisheriger Kommentar')
  })

  it('keeps timeline tool supplements beside their tool without aggregate duplicates', async () => {
    const pinia = createPinia()
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'chat', component: { render: () => null } }] })
    await router.push('/?session=parent')
    const readOnly = ref(false)
    const approval = {
      id: 'approval', sessionId: 'parent', runId: 'run', requestType: 'agent_action',
      title: 'Datei schreiben?', riskLevel: 'medium', resourceType: 'path', resourceScope: '/result.txt',
      availableDecisions: ['allow_once', 'deny'], details: { toolCallId: 'call' }
    }
    const child = {
      id: 'child', parentSessionId: 'parent', title: 'Ergebnis prüfen',
      spawnedByRunId: 'run', spawnedByToolCallId: 'call', run: { status: 'running' }
    }
    const fileAction = {
      operation: 'write', root: 'session', rootIdentity: '550e8400-e29b-41d4-a716-446655440009', path: 'result.txt',
      filename: 'result.txt', sizeBytes: 7,
      downloadUrl: '/api/workspace/files/download?path=result.txt&scope=session&session_id=550e8400-e29b-41d4-a716-446655440000&root_identity=550e8400-e29b-41d4-a716-446655440009'
    }
    root = document.createElement('div')
    document.body.append(root)
    app = createApp({
      setup: () => () => h(AgentActivityPanel, {
        displayMode: 'timeline',
        readOnly: readOnly.value,
        subagents: [child],
        history: { entries: [
          { key: 'before', type: 'commentary', message: { content: 'Vor dem Tool' } },
          {
            key: 'tool', type: 'tool', runId: 'run', approval,
            tool: { id: 'call', name: 'read_file', arguments: { path: 'result.txt' } },
            result: { fileActions: [fileAction] }
          },
          { key: 'after', type: 'commentary', message: { content: 'Nach dem Tool' } }
        ] }
      })
    })
    app.use(pinia).use(router).use(Quasar, { components })
    app.mount(root)

    const after = [...root.querySelectorAll('div')].find((element) => element.textContent === 'Nach dem Tool')
    const approvalCard = root.querySelector('.approval-card')
    const subagentList = root.querySelector('[aria-label="Subagenten"]')
    const output = root.querySelector('.work-history__outputs')
    expect({
      approval: Boolean(approvalCard),
      subagent: Boolean(subagentList),
      output: Boolean(output),
      after: Boolean(after)
    }).toEqual({ approval: true, subagent: true, output: true, after: true })
    for (const supplement of [approvalCard, subagentList, output]) {
      expect(supplement.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
    expect(root.querySelectorAll('.approval-card')).toHaveLength(1)
    expect(root.querySelectorAll('.work-history__outputs')).toHaveLength(1)

    readOnly.value = true
    await nextTick()
    expect(root.querySelector('.approval-card')).toBeNull()
    expect(root.querySelector('[aria-label="result.txt ansehen"]')).toBeNull()
    expect(root.querySelector('[aria-label="result.txt herunterladen"]')).not.toBeNull()
  })

  it('shows simultaneous child approval cards and questions in the main session without changing parent activity', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    store = useSubagentStore()
    const approvals = useApprovalStore()
    const questions = useQuestionStore()
    const chat = useChatStore()
    chat.current = { id: 'parent', messages: [] }
    chat.runId = 'parent-run'
    chat.running = true
    chat.activity = { phase: 'processing_tool', tool: 'task' }
    const pending = ['one', 'two'].map((id) => ({
      id, sessionId: 'parent', runId: `run-${id}`, requestType: 'agent_action',
      title: `Datei ${id} lesen`, riskLevel: 'low', resourceType: 'path', resourceScope: `/${id}`,
      availableDecisions: ['allow_once', 'deny'], details: {},
      payload: { subagentSessionId: `child-${id}`, subagentTitle: `Auftrag ${id}` }
    }))
    const pendingQuestions = pending.map((request) => ({ ...request, id: `question-${request.id}`, questions: [{ id: 'answer', type: 'free_text', question: `Datei ${request.id}?` }] }))
    api.get.mockImplementation(async (url) => ({ data: url === '/approvals/pending' ? pending : pendingQuestions }))
    api.post.mockResolvedValue({ data: {} })
    await approvals.fetchPending()
    await questions.fetchPending()
    const Parent = defineComponent({ setup: () => {
      const transcript = computed(() => buildChatTranscript([], approvals.forSession('parent'), { running: chat.running, questions: questions.forSession('parent') }))
      return () => h('main', [
        h('p', { role: 'status' }, agentRunPresentation({ runId: chat.runId, running: chat.running, activity: chat.activity, transcript: transcript.value }).label),
        ...transcript.value.unmatchedApprovals.map((approval) => h(ApprovalCard, { key: approval.id, approval })),
        ...questions.forSession('parent').slice(0, 1).map((request) => h(QuestionDock, { key: request.id, request, showStop: false }))
      ])
    } })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'chat', component: Parent }] })
    await router.push('/?session=parent')
    root = document.createElement('div')
    document.body.append(root)
    app = createApp({ render: () => h(RouterView) })
    app.use(pinia).use(router).use(Quasar, { components })
    app.mount(root)
    expect(root.querySelector('[role="status"]').textContent).toBe('Subagenten beauftragen')
    expect(root.querySelectorAll('.approval-card')).toHaveLength(2)
    expect(root.querySelectorAll('.question-dock')).toHaveLength(1)
    for (const id of ['one', 'two']) {
      expect(root.querySelector(`a[href="/?session=parent&subagent=child-${id}"]`).textContent).toContain(`Auftrag ${id}`)
    }
    const cards = root.querySelectorAll('.approval-card')
    const allow = [...cards[0].querySelectorAll('button')].find((button) => button.textContent.includes('Einmal erlauben'))
    allow.click()
    await vi.waitFor(() => expect(root.querySelectorAll('.approval-card')).toHaveLength(1))
    expect(root.querySelector('.approval-card').textContent).toContain('Auftrag two')
    expect(api.post).toHaveBeenCalledWith('/approvals/one/decisions', { decision: 'allow_once' })
    const answer = [...root.querySelector('.question-dock').querySelectorAll('button')].find((button) => button.textContent.includes('Antworten senden'))
    answer.click()
    await vi.waitFor(() => expect(root.querySelector('.question-dock').textContent).toContain('Auftrag two'))
    expect(api.post).toHaveBeenCalledWith('/questions/question-one/answers', { answers: { answer: [] } })
    expect(chat.running).toBe(true)
    expect(chat.runId).toBe('parent-run')
    expect(root.querySelector('[role="status"]').textContent).toBe('Subagenten beauftragen')
  })
})
