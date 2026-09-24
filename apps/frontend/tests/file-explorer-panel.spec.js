// @vitest-environment jsdom
import { createApp, nextTick, reactive } from 'vue'
import {
  ClosePopup, QBreadcrumbs, QBreadcrumbsEl, QBtn, QCard, QCardActions, QCardSection, QDialog,
  QForm, QIcon, QInput, QItem, QItemSection, QLinearProgress, QList, QMenu, QScrollArea,
  QSelect, QSeparator, QSpinner, QToolbar, QTooltip, Quasar, Ripple
} from 'quasar'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))

const { chat, workspace } = vi.hoisted(() => ({
  chat: {
    activeContext: null, current: null, selectedSessionId: 'session-a', running: false,
    sessionList: vi.fn(), fetchSessions: vi.fn(), fetchMoreSessions: vi.fn()
  },
  workspace: {
    scope: 'session', sessionId: 'session-a', directoryPath: 'reports', selectedPath: 'other.txt', selectedKind: 'file',
    toolContext: null, filter: 'hidden', entries: { reports: [] }, loading: {}, loadErrors: {}, hasMore: {}, contextVersion: 0,
    uploading: false, archiveUrl: vi.fn(), operationAvailable: vi.fn(), load: vi.fn(), configure: vi.fn(), openDirectory: vi.fn()
  }
}))

vi.mock('stores/workspace', async () => {
  const actual = await vi.importActual('stores/workspace')
  return { ...actual, useWorkspaceStore: () => workspace }
})
vi.mock('stores/chat', () => ({ useChatStore: () => chat }))
vi.mock('stores/control-center', () => ({ useControlCenterStore: () => ({ ready: false, requestGeneration: 0 }) }))
vi.mock('stores/execution-agents', () => ({ useExecutionAgentStore: () => ({ byId: () => null, workspaceContextFor: () => null }) }))
vi.mock('stores/instance-limits', () => ({ useInstanceLimitsStore: () => ({ maxUploadBytes: 1, maxUploadMib: 1 }) }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: vi.fn() }) }))
vi.mock('src/features/files/components/FileTreeNode.vue', () => ({ default: {
  props: ['entry'], emits: ['open-directory'],
  template: '<button role="treeitem" :data-file-path="entry.path" :data-current-session="entry.currentSession || undefined" @click="$emit(\'open-directory\', entry)">{{ entry.name }}</button>'
} }))

import FileExplorerPanel from '../src/features/files/components/FileExplorerPanel.vue'
import FileExplorerFooter from '../src/features/files/components/FileExplorerFooter.vue'

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
  workspace.rootIdentity = 'session-a'
  workspace.storageUsage = null
  workspace.storageUsageSessionId = null
  workspace.storageUsageLoading = false
  workspace.storageUsageFailed = false
  workspace.fetchStorageUsage = vi.fn().mockResolvedValue(null)
  chat.current = null
  chat.sessionList.mockReturnValue(null)
  chat.fetchSessions.mockResolvedValue()
  chat.fetchMoreSessions.mockResolvedValue()
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

it('starts in the session folder and opens sibling sessions through the parent project folder', async () => {
  chat.current = { id: 'session-a', title: 'Eigene', projectId: 'project-a' }
  workspace.directoryPath = '.'
  workspace.entries = { '.': [] }
  workspace.operationAvailable.mockReturnValue(true)
  chat.sessionList.mockReturnValue({
    loaded: true,
    items: [
      chat.current,
      { id: 'session-b', title: 'Andere', projectId: 'project-a' },
      { id: 'session-c', title: 'Fremd', projectId: 'project-b' }
    ]
  })
  await mountPanel()

  expect(root.textContent).not.toContain('Andere')
  root.querySelector('[aria-label="Zum übergeordneten Ordner"]').click()
  await nextTick()
  const folders = [...root.querySelectorAll('[data-file-path^="@session/"]')]
  expect(folders.map((folder) => folder.textContent)).toEqual([
    expect.stringContaining('Eigene'),
    expect.stringContaining('Andere')
  ])
  expect(folders[0].dataset.currentSession).toBe('true')
  expect(folders[1].dataset.currentSession).toBeUndefined()
  expect(root.textContent).not.toContain('Fremd')
  folders[1].click()

  await vi.waitFor(() => expect(workspace.configure).toHaveBeenLastCalledWith(expect.objectContaining({
    sessionId: 'session-a',
    rootIdentity: 'session-b',
    scope: 'session'
  })))
})

it('distinguishes duplicate session folders and returns to the current session when one leaves the project', async () => {
  chat.current = { id: 'session-a', title: 'Gleich', projectId: 'project-a' }
  workspace.directoryPath = '.'
  workspace.entries = { '.': [] }
  workspace.operationAvailable.mockReturnValue(true)
  const list = reactive({
    loaded: true,
    items: [
      chat.current,
      { id: 'session-b', title: 'Gleich', projectId: 'project-a' },
      { id: 'session-c', title: '', projectId: 'project-a' },
      { id: 'session-d', title: '', projectId: 'project-a' }
    ]
  })
  chat.sessionList.mockReturnValue(list)
  await mountPanel()

  root.querySelector('[aria-label="Zum übergeordneten Ordner"]').click()
  await nextTick()
  const folders = [...root.querySelectorAll('[data-file-path^="@session/"]')]
  expect(folders.map((folder) => folder.textContent.trim())).toEqual([
    'Gleich · session-a',
    'Gleich · session-b',
    'Unbenannte Session · session-c',
    'Unbenannte Session · session-d'
  ])
  folders[1].click()
  await vi.waitFor(() => expect(workspace.configure).toHaveBeenLastCalledWith(expect.objectContaining({
    rootIdentity: 'session-b'
  })))

  list.items = list.items.filter((session) => session.id !== 'session-b')

  await vi.waitFor(() => expect(workspace.configure).toHaveBeenLastCalledWith(expect.objectContaining({
    rootIdentity: 'session-a'
  })))
})

it('retries storage usage for the selected session root', async () => {
  chat.current = { id: 'session-a' }
  workspace.rootIdentity = 'session-b'
  workspace.storageUsageSessionId = 'session-b'
  workspace.storageUsageFailed = true
  app = createApp(FileExplorerFooter)
  app.use(Quasar, { components, directives: { ClosePopup, Ripple } })
  app.mount(root)
  await nextTick()
  workspace.fetchStorageUsage.mockClear()

  root.querySelector('[aria-label="Speichernutzung erneut laden"]').click()

  expect(workspace.fetchStorageUsage).toHaveBeenCalledWith('session-b')
})
