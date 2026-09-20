// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QExpansionItem, QForm, QIcon, QInput, QItem, QItemLabel, QItemSection, QList, QSelect, QSeparator, QSpace, QToggle, QTooltip } from 'quasar'
import ApprovalCard from '../src/components/ApprovalCard.vue'
import { useApprovalStore } from '../src/stores/approvals'
import { api } from 'src/boot/axios'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
vi.mock('components/SubagentRequestLink.vue', () => ({ default: { render: () => null } }))

let app, root
afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.clearAllMocks()
})

it.each(['read', 'write'])('displays historical terminal approval data generically with %s classification', async (classification) => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const approvals = useApprovalStore()
  api.post.mockResolvedValue({ data: {} })
  const command = 'printf reviewed'
  const currentDirectory = '/home/user/terminal'
  const approval = {
    id: 'shared', sessionId: 'session-1', requestType: 'agent_action', actionKind: 'shell_run',
    title: 'Befehl ausführen?', summary: 'Befehl prüfen', reason: 'Der Befehl prüft die Projektdateien.', riskLevel: classification === 'read' ? 'low' : 'high',
    capability: `shell.${classification}`, resourceType: 'shell_command', resourceDisplay: currentDirectory,
    availableDecisions: ['allow_once', 'allow_session', 'deny'],
    payload: {
      sharedDraft: { command, currentDirectory, inspectionRevision: 'legacy-revision' },
      shellClassification: classification,
      shellAnalysisComplete: true,
      shellPrefixes: [['ls', '-l']],
      displayItems: [
        { label: 'Shell-Befehl', value: command, format: 'code' },
        { label: 'Terminal-Ordner', value: currentDirectory, format: 'code' },
        { label: 'Für diese Session', value: ['Alle passenden lesenden Datei- und Shell-Aktionen: /extern und Unterordner'], format: 'list' }
      ]
    },
    details: { tool: 'shell_run', arguments: { command: 'unreviewed original' } }
  }
  approvals.add(approval)
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => approvals.pending[0] ? h(ApprovalCard, { approval: approvals.pending[0] }) : null })
  app.use(pinia).use(Quasar, { components: { QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QExpansionItem, QForm, QIcon, QInput, QItem, QItemLabel, QItemSection, QList, QSelect, QSeparator, QSpace, QToggle, QTooltip } })
  app.mount(root)
  await nextTick()
  expect(root.querySelector('.tool-call__details').textContent).toBe(command)
  expect(root.textContent).toContain(currentDirectory)
  expect(root.textContent).toContain('Begründung des Agenten: Der Befehl prüft die Projektdateien.')
  expect(root.textContent).toContain('Alle passenden lesenden Datei- und Shell-Aktionen: /extern und Unterordner')
  expect(root.querySelector('textarea, [contenteditable="true"]')).toBeNull()
  expect(root.textContent).toContain(classification === 'read' ? 'Keine Schreibaktion erkannt' : 'Als schreibend eingestuft')
  expect(root.textContent).not.toContain('Analyse unvollständig')
  expect(root.textContent).toContain('Erkannte Befehlspräfixe')
  expect(root.textContent).not.toContain('Session-Präfixe')
  expect(root.textContent).toContain('ls -l')
  expect(api.post).not.toHaveBeenCalled()
  root.querySelector('.q-checkbox').click()
  await nextTick()
  const allow = [...root.querySelectorAll('button')].find((button) => button.textContent === 'Für Session erlauben')
  expect(allow).toBeTruthy()
  allow.click()
  await nextTick()
  expect(api.post).toHaveBeenCalledWith('/approvals/shared/decisions', {
    decision: 'allow_session'
  })
})

it('edits a shell command and approves the final value in one action', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const approvals = useApprovalStore()
  api.post.mockResolvedValue({ data: { status: 'approved' } })
  approvals.add({
    id: 'editable', sessionId: 'session-1', requestType: 'agent_action', actionKind: 'shell_run', status: 'pending',
    title: 'Befehl ausführen?', summary: 'Befehl prüfen', riskLevel: 'low',
    capability: 'shell.read', resourceType: 'shell_command', resourceDisplay: '/workspace',
    availableDecisions: ['allow_once', 'allow_session', 'deny'],
    payload: {
      shellClassification: 'read', shellAnalysisComplete: true, shellPrefixes: [['ls']],
      argumentEdit: {
        revision: 'a'.repeat(64),
        fields: [{
          path: ['command'], label: 'Befehl', inputType: 'textarea', required: true,
          maxLength: 262144, value: 'ls'
        }]
      }
    },
    details: { tool: 'shell_run', arguments: { command: 'ls' } }
  })
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => approvals.pending[0] ? h(ApprovalCard, { approval: approvals.pending[0] }) : null })
  app.use(pinia).use(Quasar, { components: { QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip, QExpansionItem, QForm, QIcon, QInput, QItem, QItemLabel, QItemSection, QList, QSelect, QSeparator, QSpace, QToggle, QTooltip } })
  app.mount(root)
  await nextTick()

  ;[...root.querySelectorAll('button')].find((button) => button.textContent.includes('Argumente bearbeiten')).click()
  await nextTick()
  const command = root.querySelector('textarea')
  command.value = 'pwd'
  command.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()

  expect(root.textContent).not.toContain('Keine Schreibaktion erkannt')
  expect(root.querySelector('.q-checkbox')).toBeNull()
  const allow = [...root.querySelectorAll('button')].find((button) => button.textContent.includes('Änderungen einmal erlauben'))
  allow.click()
  await vi.waitFor(() => expect(api.post).toHaveBeenCalled())
  expect(api.post).toHaveBeenCalledWith('/approvals/editable/decisions', {
    decision: 'allow_once',
    argument_revision: 'a'.repeat(64),
    argument_edits: [{ path: ['command'], value: 'pwd' }]
  })
})
