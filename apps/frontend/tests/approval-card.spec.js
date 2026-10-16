// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { Quasar } from 'quasar'
import { quasarOptions } from './quasar-options'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm }) }))

import ApprovalCard from '../src/features/approvals/ApprovalCard.vue'
import SessionApp from '../src/features/chat/components/SessionApp.vue'
import MemorySettingsPage from '../src/features/memory/MemorySettingsPage.vue'
import { useApprovalStore } from '../src/stores/approvals'
import { useChatStore } from '../src/stores/chat'
import { api } from 'src/boot/axios'

const { confirm } = vi.hoisted(() => ({ confirm: vi.fn() }))
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
    }]
  }
  api.get.mockImplementation(async (url) => ({ data: responses[url] }))
  api.post.mockResolvedValue({ data: {} })
})

afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.unstubAllGlobals()
  delete Element.prototype.scrollIntoView
})

async function mountApproval(approval = responses['/approvals/pending'][0]) {
  const store = useApprovalStore()
  store.pending = [approval]
  await mountView(() => store.pending.map((approval) => h(ApprovalCard, { approval, showDraftAction: false })))
}

async function mountView(render) {
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
  app = createApp({ render })
  app.use(pinia).use(router).use(Quasar, quasarOptions)
  app.mount(root)
  await nextTick()
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
  const choice = [...document.querySelectorAll('[role="option"]')].find((item) => item.querySelector('.q-item__label')?.textContent === option)
  choice.click()
  await vi.waitFor(() => expect(field.textContent).toContain(option))
  await vi.waitFor(() => expect(document.querySelector('[role="option"]')).toBeNull())
}

describe('approval card', () => {
  it('shows session grants with their exact shell context', async () => {
    useChatStore().current = { id: 'session-1' }
    responses['/approvals/permissions'] = [{
      id: 'grant-1', sessionId: 'session-1', applicable: false,
      capability: 'resource.write', resourceType: 'shell_action', resourceDisplay: '/opt/project',
      executionAgentName: 'Notebook', currentDirectory: '/opt/project',
      command: 'printf reviewed > output.txt', elevated: true
    }]
    await mountView(() => h(SessionApp))
    await vi.waitFor(() => expect(root.querySelector('[aria-labelledby="session-permissions-title"]')?.textContent).toMatch(
      /Exakter Shell-Befehl · Schreiben.*Notebook.*\/opt\/project.*printf reviewed > output.txt.*Mit Administratorrechten.*Inaktiv/s
    ))
  })

  it('hides stale grant binding claims after a failed refresh on computer change', async () => {
    useChatStore().current = { id: 'session-1', preferredExecutionAgentId: 'old' }
    responses['/approvals/permissions'] = [{
      id: 'grant-1', sessionId: 'session-1', applicable: true,
      capability: 'resource.read', resourceType: 'execution_path', resourceDisplay: '/work/file'
    }]
    await mountView(() => h(SessionApp))
    const section = root.querySelector('[aria-labelledby="session-permissions-title"]')
    await vi.waitFor(() => expect(section.textContent).toContain('/work/file'))
    api.get.mockRejectedValue(new Error('offline'))
    useChatStore().current.preferredExecutionAgentId = 'new'
    await vi.waitFor(() => expect(section.textContent).toContain('Session-Freigaben konnten nicht geladen werden.'))
    expect(section.textContent).not.toContain('/work/file')
  })

  it('keeps loaded grants mounted during background refresh of an unchanged session', async () => {
    const chat = useChatStore()
    chat.current = { id: 'session-1', sessionProfile: { agentIds: ['plan'] } }
    responses['/approvals/permissions'] = [{ id: 'grant-1', sessionId: 'session-1', resourceDisplay: '/work/file' }]
    await mountView(() => h(SessionApp))
    const section = root.querySelector('[aria-labelledby="session-permissions-title"]')
    const list = await vi.waitUntil(() => section.querySelector('.q-list'))
    let finishRefresh
    api.get.mockImplementation(() => new Promise((resolve) => { finishRefresh = resolve }))
    chat.current = JSON.parse(JSON.stringify(chat.current))
    await nextTick()
    const refresh = useApprovalStore().fetchGrants()
    await nextTick()
    expect(section.querySelector('.q-list')).toBe(list)
    finishRefresh({ data: responses['/approvals/permissions'] })
    await refresh
  })

  it('submits administrator session approval without collecting a password', async () => {
    responses['/approvals/pending'][0] = {
      ...responses['/approvals/pending'][0], requestType: 'agent_action', resourceType: 'administrator_path',
      capability: 'filesystem.write', availableDecisions: ['allow_once', 'allow_session', 'deny'],
      payload: {}
    }
    await mountApproval()
    const card = root.querySelector('.approval-card')
    expect(card.querySelector('input[type="password"]')).toBeNull()
    card.querySelector('.q-checkbox').click()
    await nextTick()
    button(card, 'Für Session erlauben').click()
    await vi.waitFor(() => expect(api.post).toHaveBeenCalledExactlyOnceWith('/approvals/approval/decisions', {
      decision: 'allow_session'
    }))
  })

  it.each(['read', 'write'])('displays shell approval details with %s classification', async (classification) => {
    const command = 'printf reviewed'
    const currentDirectory = '/home/user/terminal'
    await mountApproval({
      id: 'shell', sessionId: 'session-1', requestType: 'agent_action', actionKind: 'shell_run',
      title: 'Befehl ausführen?', summary: 'Befehl prüfen', reason: 'Der Befehl prüft die Projektdateien.', riskLevel: classification === 'read' ? 'low' : 'high',
      capability: `shell.${classification}`, resourceType: 'shell_command', resourceDisplay: currentDirectory,
      availableDecisions: ['allow_once', 'allow_session', 'deny'],
      payload: {
        shellClassification: classification, shellAnalysisComplete: true, shellPrefixes: [['ls', '-l']],
        displayItems: [
          { label: 'Shell-Befehl', value: command, format: 'code' },
          { label: 'Terminal-Ordner', value: currentDirectory, format: 'code' },
          { label: 'Freigegebener Bereich', value: ['Exakter Shell-Befehl auf diesem Computer mit gleichem Arbeitsverzeichnis und gleicher Umgebung'], format: 'list' }
        ]
      },
      details: { tool: 'shell_run', arguments: { command: 'unreviewed original' } }
    })
    expect(root.querySelector('.tool-call__details').textContent).toBe(command)
    expect(root.textContent).toContain(currentDirectory)
    expect(root.textContent).toContain('Begründung des Agenten: Der Befehl prüft die Projektdateien.')
    expect(root.querySelector('textarea, [contenteditable="true"]')).toBeNull()
    expect(root.textContent).toContain(classification === 'read' ? 'Keine Schreibaktion erkannt' : 'Als schreibend eingestuft')
    expect(root.textContent).not.toContain('Analyse unvollständig')
    expect(root.textContent).not.toContain('Erkannte Befehlspräfixe')
    expect(root.textContent).not.toContain('Technische Details')
    expect(api.post).not.toHaveBeenCalled()
    root.querySelector('.q-checkbox').click()
    await nextTick()
    expect(root.textContent).toContain('Exakter Shell-Befehl auf diesem Computer mit gleichem Arbeitsverzeichnis und gleicher Umgebung')
    const allow = [...root.querySelectorAll('button')].find((button) => button.textContent === 'Für Session erlauben')
    expect(allow).toBeTruthy()
    allow.click()
    await nextTick()
    expect(api.post).toHaveBeenCalledWith('/approvals/shell/decisions', { decision: 'allow_session' })
  })

  it('shows long memory content in tool details', async () => {
    const content = 'Meine Docker-Stacks sollen über Arcane verwaltet werden. '.repeat(300)
    await mountApproval({
      id: 'memory', requestType: 'agent_action', capability: 'memory.write',
      title: 'Memory speichern?', availableDecisions: ['allow_once', 'allow_session', 'deny'],
      details: { tool: 'memory_write', arguments: { content } }
    })
    expect(root.querySelector('.tool-call__details').textContent).toBe(content)
  })

  it('edits a shell command and approves the final value in one action', async () => {
    api.post.mockResolvedValue({ data: { status: 'approved' } })
    await mountApproval({
      id: 'editable', sessionId: 'session-1', requestType: 'agent_action', actionKind: 'shell_run', status: 'pending',
      title: 'Befehl ausführen?', summary: 'Befehl prüfen', riskLevel: 'low',
      capability: 'shell.read', resourceType: 'shell_command', resourceDisplay: '/workspace',
      availableDecisions: ['allow_once', 'allow_session', 'deny'],
      payload: {
        shellClassification: 'read', shellAnalysisComplete: true, shellPrefixes: [['ls']],
        argumentEdit: {
          revision: 'a'.repeat(64),
          fields: [{ path: ['command'], label: 'Befehl', inputType: 'textarea', required: true, maxLength: 262144, value: 'ls' }]
        }
      },
      details: { tool: 'shell_run', arguments: { command: 'ls' } }
    })
    button(root, 'Argumente bearbeiten').click()
    await nextTick()
    const command = root.querySelector('textarea')
    command.value = 'pwd'
    command.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(root.textContent).not.toContain('Keine Schreibaktion erkannt')
    expect(root.querySelector('.q-checkbox')).toBeNull()
    button(root, 'Für diesen Lauf erlauben').click()
    await vi.waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(api.post).toHaveBeenCalledWith('/approvals/editable/decisions', {
      decision: 'allow_once', argument_revision: 'a'.repeat(64),
      argument_edits: [{ path: ['command'], value: 'pwd' }]
    })
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

  async function openEdit() {
    await mountApproval()
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
    app.use(pinia).use(router).use(Quasar, quasarOptions)
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
    await mountApproval()
    expect(root.querySelector('[aria-label="Vorschlag bearbeiten"]')).toBeNull()
    expect(root.querySelector('.approval-card form')).toBeNull()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('submits all four fields only on approval and retains them after failure', async () => {
    const card = await openEdit()
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
