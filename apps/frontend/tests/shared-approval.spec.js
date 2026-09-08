// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QExpansionItem, QIcon, QItem, QItemLabel, QItemSection, QList, QSeparator, QSpace } from 'quasar'
import ApprovalCard from '../src/components/ApprovalCard.vue'
import { useApprovalStore } from '../src/stores/approvals'
import { api } from 'src/boot/axios'
import { sharedDraft } from './terminal-websocket'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
vi.mock('components/SubagentRequestLink.vue', () => ({ default: { render: () => null } }))

let app, root
afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.clearAllMocks()
})

it.each(['read', 'write'])('displays the exact read-only shared review and actual %s classification and grants', async (classification) => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const approvals = useApprovalStore()
  api.post.mockResolvedValue({ data: {} })
  const approval = {
    id: 'shared', sessionId: 'session-1', requestType: 'agent_action', actionKind: 'shell_run',
    title: 'Befehl ausführen?', summary: 'Befehl prüfen', riskLevel: classification === 'read' ? 'low' : 'high',
    capability: `shell.${classification}`, resourceType: 'shell_command', resourceDisplay: sharedDraft.currentDirectory,
    availableDecisions: ['allow_once', 'allow_session', 'deny'],
    payload: { sharedDraft, shellClassification: classification, shellAnalysisComplete: true, shellPrefixes: [['ls', '-l']] },
    details: { tool: 'shell_run', arguments: { command: 'unreviewed original' } }
  }
  approvals.add(approval)
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => approvals.pending[0] ? h(ApprovalCard, { approval: approvals.pending[0] }) : null })
  app.use(pinia).use(Quasar, { components: { QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QExpansionItem, QIcon, QItem, QItemLabel, QItemSection, QList, QSeparator, QSpace } })
  app.mount(root)
  await nextTick()
  expect(root.querySelector('.tool-call__details').textContent).toBe(sharedDraft.command)
  expect(root.textContent).toContain(sharedDraft.currentDirectory)
  expect(root.querySelector('textarea, [contenteditable="true"]')).toBeNull()
  expect(root.textContent).toContain(classification === 'read' ? 'Keine Schreibaktion erkannt' : 'Als schreibend eingestuft')
  expect(root.textContent).not.toContain('Analyse unvollständig')
  expect(root.textContent).toContain('ls -l')
  const changed = { ...approval, payload: { ...approval.payload, sharedDraft: {
    ...sharedDraft, command: 'printf edited', currentDirectory: '/new/terminal/cwd', inspectionRevision: 'inspection-new'
  } } }
  approvals.update(changed)
  await nextTick()
  expect(root.querySelector('.tool-call__details').textContent).toBe('printf edited')
  expect(root.textContent).toContain('/new/terminal/cwd')
  expect(api.post).not.toHaveBeenCalled()
  root.querySelector('.q-checkbox').click()
  await nextTick()
  const allow = [...root.querySelectorAll('button')].find((button) => button.textContent === 'Für Session erlauben')
  expect(allow).toBeTruthy()
  allow.click()
  await nextTick()
  expect(api.post).toHaveBeenCalledWith('/approvals/shared/decisions', {
    decision: 'allow_session', expected_revision: 'inspection-new'
  })
})
