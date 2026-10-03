// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Quasar, QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QDialog, QExpansionItem, QForm, QIcon, QInput, QItem, QItemLabel, QItemSection, QList, QScrollArea, QSelect, QSeparator, QSkeleton, QSpace, QSpinner, QToggle, QTooltip } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } }))
vi.mock('src/features/chat/components/ChatComposer.vue', () => ({ default: { methods: { focus() {} }, render: () => null } }))
vi.mock('src/app/control-center/ControlCenterPanel.vue', () => ({ default: { render: () => null } }))
vi.mock('src/features/chat/components/ChatQueuedMessages.vue', () => ({ default: { render: () => null } }))
vi.mock('src/features/chat/components/MessageContent.vue', () => ({ default: { render: () => null } }))
vi.mock('src/features/chat/components/MessageAttachments.vue', () => ({ default: { render: () => null } }))

import ChatPage from '../src/features/chat/ChatPage.vue'
import { api } from 'src/boot/axios'
import { useApprovalStore } from '../src/stores/approvals'
import { useChatStore } from '../src/stores/chat'
import { useControlCenterStore } from '../src/stores/control-center'
import { useWorkspaceStore } from '../src/stores/workspace'

let app, root, chat
const initialRevision = 'a'.repeat(64)
const parentRevision = 'b'.repeat(64)
const source = '{"name":"Dateien prüfen","instructions":"Nur lesen"}'

async function mountPage({ actionKind = 'build_skill', rootIdentity, reply } = {}) {
  vi.stubGlobal('scrollTo', vi.fn())
  const pinia = createPinia()
  setActivePinia(pinia)
  chat = useChatStore()
  chat.outboxLoaded = true
  const approvals = useApprovalStore()
  const workspace = useWorkspaceStore()
  const controlCenter = useControlCenterStore()
  const approval = {
    id: 'skill-approval', sessionId: 'parent', runId: 'child-run', actionKind,
    requestType: 'agent_action', riskLevel: 'low', title: 'Skill speichern',
    availableDecisions: ['allow_once', 'deny'],
    payload: { subagentSessionId: 'child', subagentTitle: 'Skill erstellen' },
    details: {
      skillPreview: { name: 'Dateien prüfen', description: 'Dateien lesen', instructions: 'Nur lesen' },
      draftFile: { root: 'session', path: 'skill-draft.json', filename: 'skill-draft.json', initialRevision, ...(rootIdentity ? { rootIdentity } : {}) }
    }
  }
  api.post.mockResolvedValue({ data: {} })
  api.get.mockImplementation(async (url) => {
    if (url === '/workspace/files/preview') return reply ? await reply() : { data: { kind: 'text', content: source, revision: initialRevision } }
    if (url === '/sessions/parent') return { data: { id: 'parent', projectId: 'parent-project', messages: [] } }
    if (url === '/approvals/pending') return { data: [approval] }
    if (url === '/questions/pending' || url.endsWith('/children')) return { data: [] }
    return { data: {} }
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'chat', component: ChatPage }] })
  await router.push('/?session=parent&project=parent-project')
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(ChatPage) })
  app.use(pinia).use(router).use(Quasar, { components: { QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QDialog, QExpansionItem, QForm, QIcon, QInput, QItem, QItemLabel, QItemSection, QList, QScrollArea, QSelect, QSeparator, QSkeleton, QSpace, QSpinner, QToggle, QTooltip } })
  app.component('QPage', { render() { return h('div', this.$slots.default?.()) } })
  app.component('QSplitter', { render() { return h('div', this.$slots.before?.()) } })
  app.component('QVirtualScroll', { render() { return h('div', this.$slots.after?.()) } })
  app.mount(root)
  await vi.waitFor(() => expect(root.querySelector('.approval-card')).not.toBeNull())
  workspace.sessionId = 'parent'
  workspace.rootIdentity = 'parent'
  workspace.scope = 'session'
  workspace.viewedEntry = { path: 'skill-draft.json' }
  workspace.editorActive = true
  workspace.editorDirty = true
  workspace.editorDraft = 'Unsaved parent draft'
  workspace.filePreviews = { 'skill-draft.json': { content: 'Parent file', revision: parentRevision } }
  await nextTick()
  return {
    approval, approvals, workspace,
    preview: vi.spyOn(approvals, 'previewSkillDraft'),
    openFile: vi.spyOn(controlCenter, 'openFile'),
    startEdit: vi.spyOn(workspace, 'startFileTabEdit')
  }
}

function click(label, container = root) {
  const button = [...container.querySelectorAll('button')].find((item) => item.textContent.includes(label) || item.getAttribute('aria-label') === label)
  expect(button, label).toBeTruthy()
  expect(button.disabled).toBe(false)
  button.click()
}

afterEach(() => {
  app?.unmount()
  chat?.cancelQueueDrain()
  root?.remove()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

describe('child skill draft previews', () => {
  it.each([
    ['build_skill', undefined],
    ['edit_skill', 'parent-project']
  ])('opens %s from its child session read-only (explicit root=%s)', async (actionKind, rootIdentity) => {
    const { approval, workspace, preview, openFile, startEdit } = await mountPage({ actionKind, rootIdentity })
    const parentPreviews = workspace.filePreviews
    click('Entwurf ansehen')
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"] pre')?.textContent).toBe(source))
    expect(preview).toHaveBeenCalledWith(expect.objectContaining({ id: approval.id, payload: approval.payload }))
    expect(api.get).toHaveBeenCalledWith('/workspace/files/preview', {
      params: { scope: 'session', session_id: 'child', path: 'skill-draft.json', ...(rootIdentity ? { root_identity: rootIdentity } : {}) }
    })
    const dialog = document.querySelector('[role="dialog"]')
    expect(dialog.textContent).toContain('Nur Lesezugriff')
    expect(dialog.querySelector('input, textarea, [contenteditable="true"]')).toBeNull()
    expect(dialog.textContent).not.toMatch(/Bearbeiten|Speichern/)
    expect(openFile).not.toHaveBeenCalled()
    expect(startEdit).not.toHaveBeenCalled()
    expect(api.patch).not.toHaveBeenCalled()
    expect(workspace.sessionId).toBe('parent')
    expect(workspace.rootIdentity).toBe('parent')
    expect(workspace.filePreviews).toBe(parentPreviews)
    expect(workspace.editorDraft).toBe('Unsaved parent draft')
    expect(workspace.editorDirty).toBe(true)
    click('Entwurfsvorschau schließen', dialog)
    await nextTick()
    click('Für diesen Lauf erlauben')
    await vi.waitFor(() => expect(api.post).toHaveBeenCalledWith('/approvals/skill-approval/decisions', {
      decision: 'allow_once', expected_revision: initialRevision
    }))
  })

  it('does not present changed bytes as the initially approved draft', async () => {
    await mountPage({ reply: async () => ({ data: { kind: 'text', content: 'Changed source', revision: parentRevision } }) })
    click('Entwurf ansehen')
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"] [role="alert"]')).not.toBeNull())
    expect(document.querySelector('[role="dialog"]').textContent).toContain('Die Datei wurde seit dem Öffnen geändert')
    expect(document.querySelector('[role="dialog"]').textContent).not.toContain('Changed source')
  })

  it('can approve the initial inline skill preview without opening the draft file', async () => {
    const { preview } = await mountPage()
    click('Für diesen Lauf erlauben')
    await vi.waitFor(() => expect(api.post).toHaveBeenCalledWith('/approvals/skill-approval/decisions', {
      decision: 'allow_once', expected_revision: initialRevision
    }))
    expect(preview).not.toHaveBeenCalled()
  })

  it('ignores late preview results after the dialog closes', async () => {
    let resolve
    const { preview } = await mountPage({ reply: () => new Promise((done) => { resolve = done }) })
    click('Entwurf ansehen')
    await vi.waitFor(() => expect(preview).toHaveBeenCalledOnce())
    click('Entwurfsvorschau schließen', document.querySelector('[role="dialog"]'))
    resolve({ data: { kind: 'text', content: source, revision: initialRevision } })
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull())
    expect(root.textContent).not.toContain(source)
  })
})
