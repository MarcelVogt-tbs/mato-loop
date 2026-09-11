// @vitest-environment jsdom
import { createApp, nextTick } from 'vue'
import {
  ClosePopup, QBreadcrumbs, QBreadcrumbsEl, QBtn, QCard, QCardActions, QCardSection, QDialog,
  QForm, QIcon, QInput, QItem, QItemSection, QLinearProgress, QList, QMenu, QScrollArea,
  QSelect, QSeparator, QSpinner, QToolbar, QTooltip, Quasar, Ripple
} from 'quasar'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))

const { workspace } = vi.hoisted(() => ({
  workspace: {
    scope: 'session', sessionId: 'session-a', directoryPath: 'reports', selectedPath: 'other.txt', selectedKind: 'file',
    toolContext: null, filter: 'hidden', entries: { reports: [] }, loading: {}, loadErrors: {}, hasMore: {}, contextVersion: 0,
    uploading: false, archiveUrl: vi.fn(), operationAvailable: vi.fn(), load: vi.fn(), configure: vi.fn()
  }
}))

vi.mock('stores/workspace', async () => {
  const actual = await vi.importActual('stores/workspace')
  return { ...actual, useWorkspaceStore: () => workspace }
})
vi.mock('stores/chat', () => ({ useChatStore: () => ({ activeContext: null, current: null, selectedSessionId: 'session-a', running: false }) }))
vi.mock('stores/control-center', () => ({ useControlCenterStore: () => ({ ready: false, requestGeneration: 0 }) }))
vi.mock('stores/execution-agents', () => ({ useExecutionAgentStore: () => ({ byId: () => null, workspaceContextFor: () => null }) }))
vi.mock('stores/instance-limits', () => ({ useInstanceLimitsStore: () => ({ maxUploadBytes: 1, maxUploadMib: 1 }) }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: vi.fn() }) }))
vi.mock('components/FileTreeNode.vue', () => ({ default: { template: '<div />' } }))

import FileExplorerPanel from '../src/components/FileExplorerPanel.vue'

const components = {
  QBreadcrumbs, QBreadcrumbsEl, QBtn, QCard, QCardActions, QCardSection, QDialog, QForm,
  QIcon, QInput, QItem, QItemSection, QLinearProgress, QList, QMenu, QScrollArea, QSelect,
  QSeparator, QSpinner, QToolbar, QTooltip
}
let app, root

beforeEach(() => {
  vi.clearAllMocks()
  workspace.scope = 'session'
  workspace.sessionId = 'session-a'
  workspace.directoryPath = 'reports'
  workspace.selectedPath = 'other.txt'
  workspace.selectedKind = 'file'
  workspace.toolContext = null
  workspace.filter = 'hidden'
  workspace.entries = { reports: [] }
  workspace.archiveUrl.mockReturnValue('/api/workspace/files/download?path=reports&archive=zip')
  workspace.operationAvailable.mockReturnValue(false)
  root = document.createElement('div')
  document.body.append(root)
})

afterEach(() => {
  app?.unmount()
  root?.remove()
  document.querySelectorAll('.q-menu').forEach((menu) => menu.remove())
})

async function mountPanel() {
  app = createApp(FileExplorerPanel)
  app.use(Quasar, { components, directives: { ClosePopup, Ripple } })
  app.mount(root)
  await nextTick()
}

it.each([
  ['read-only session', 'session'],
  ['computer', 'filesystem']
])('shows the current-directory archive action in the %s view', async (_, scope) => {
  workspace.scope = scope
  await mountPanel()

  expect(root.querySelector('[aria-label="Weitere Dateiaktionen"]')).not.toBeNull()
  root.querySelector('[aria-label="Weitere Dateiaktionen"]').click()
  await vi.waitFor(() => expect(document.querySelector('.q-menu')).not.toBeNull())
  const action = [...document.querySelectorAll('.q-menu .q-item')]
    .find((item) => item.textContent.includes('Ordner als ZIP herunterladen'))

  expect(workspace.archiveUrl).toHaveBeenCalledWith('reports')
  expect(action).toMatchObject({ tagName: 'A' })
  expect(action.getAttribute('href')).toBe('/api/workspace/files/download?path=reports&archive=zip')
  expect(action.hasAttribute('download')).toBe(true)
})

it('hides the archive action when its computer operations are unavailable', async () => {
  workspace.scope = 'filesystem'
  workspace.archiveUrl.mockReturnValue('')
  await mountPanel()

  expect(root.querySelector('[aria-label="Weitere Dateiaktionen"]')).toBeNull()
})

it('hides the archive action in a historical tool context', async () => {
  workspace.scope = 'filesystem'
  workspace.toolContext = { executionAgentId: 'agent-1', currentDirectory: '/project' }
  workspace.archiveUrl.mockReturnValue('')
  await mountPanel()

  expect(root.querySelector('[aria-label="Weitere Dateiaktionen"]')).toBeNull()
})
