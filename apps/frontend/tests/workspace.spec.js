import { createPinia, setActivePinia } from 'pinia'
import { markRaw } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: {
    delete: vi.fn(),
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
    put: vi.fn()
  }
}))

vi.mock('src/boot/axios', () => ({ api }))

import {
  workspaceContextKey,
  withFileSuffix,
  useWorkspaceStore
} from '../src/stores/workspace'
import { parentPath, persistedFileView } from '../src/features/workspace/state'
import { useControlCenterStore } from '../src/stores/control-center'

function liveContext(operations) {
  return {
    executionAgentId: 'agent-1',
    connected: true,
    operations
  }
}

describe('workspace store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('compares workspace contexts by value', () => {
    const context = {
      executionAgentId: 'agent-1',
      connected: true,
      operations: ['filesystem.list', 'filesystem.read']
    }

    expect(workspaceContextKey({ ...context, operations: [...context.operations] }))
      .toBe(workspaceContextKey(context))
    expect(workspaceContextKey({ ...context, connected: false }))
      .not.toBe(workspaceContextKey(context))
  })

  it('isolates the single files tree when the session changes', async () => {
    api.get
      .mockResolvedValueOnce({ data: { entries: [{ path: 'old.txt' }] } })
      .mockResolvedValueOnce({ data: { entries: [] } })
    const workspace = useWorkspaceStore()

    workspace.configure({
      sessionId: 'session-a',
      currentDirectory: '/project',
      workspaceContext: liveContext(['filesystem.list'])
    })
    await workspace.load('.')
    workspace.openEntry({ kind: 'file', path: 'old.txt' })
    workspace.filter = 'old'
    workspace.configure({ sessionId: 'session-b' })
    await workspace.load('.')

    expect(workspace.entries).toEqual({ '.': [] })
    expect(workspace.selectedPath).toBe('.')
    expect(workspace.viewedEntry).toBeNull()
    expect(workspace.filter).toBe('')
    expect(api.get).toHaveBeenLastCalledWith('/workspace/entries', {
      params: { path: '.', scope: 'session', session_id: 'session-b' },
      suppressErrorToast: true
    })
  })

  it('loads storage usage without automatic error toasts', async () => {
    let resolveUsage
    api.get.mockImplementationOnce(() => new Promise((resolve) => { resolveUsage = resolve }))
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })

    const request = workspace.fetchStorageUsage('session-a')
    expect(workspace.storageUsageLoading).toBe(true)
    resolveUsage({ data: { scope: 'session', usedBytes: 12, quotaBytes: 500 } })

    await expect(request).resolves.toEqual({ scope: 'session', usedBytes: 12, quotaBytes: 500 })
    expect(workspace.storageUsageLoading).toBe(false)
    expect(workspace.storageUsageFailed).toBe(false)
    expect(api.get).toHaveBeenCalledWith('/workspace/usage', {
      params: { session_id: 'session-a' },
      suppressErrorToast: true
    })
  })

  it('ignores stale usage responses and clears usage on session switches', async () => {
    let resolveStale
    api.get
      .mockImplementationOnce(() => new Promise((resolve) => { resolveStale = resolve }))
      .mockResolvedValueOnce({ data: { scope: 'project', usedBytes: 20, quotaBytes: 500 } })
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })
    const staleRequest = workspace.fetchStorageUsage('session-a')

    workspace.configure({ sessionId: 'session-b' })
    expect(workspace.storageUsage).toBeNull()
    await workspace.fetchStorageUsage('session-b')
    resolveStale({ data: { scope: 'session', usedBytes: 10, quotaBytes: 100 } })
    await staleRequest

    expect(workspace.storageUsageSessionId).toBe('session-b')
    expect(workspace.storageUsage).toEqual({ scope: 'project', usedBytes: 20, quotaBytes: 500 })
  })

  it('clears storage usage when a session moves to another managed root', async () => {
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a', rootIdentity: 'session-a' })
    workspace.storageUsageSessionId = 'session-a'
    workspace.storageUsage = { scope: 'session', usedBytes: 10, quotaBytes: 100 }

    workspace.configure({ sessionId: 'session-a', rootIdentity: 'project-a' })

    expect(workspace.storageUsageSessionId).toBeNull()
    expect(workspace.storageUsage).toBeNull()
  })

  it('keeps usage failures quiet and retryable', async () => {
    api.get.mockRejectedValueOnce(new Error('unavailable'))
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })

    await expect(workspace.fetchStorageUsage()).resolves.toBeNull()

    expect(workspace.storageUsage).toBeNull()
    expect(workspace.storageUsageLoading).toBe(false)
    expect(workspace.storageUsageFailed).toBe(true)
  })

  it('uploads selected files to the session root and reloads it', async () => {
    api.post.mockResolvedValue({ data: {} })
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a', currentDirectory: null })
    vi.clearAllMocks()
    api.post.mockResolvedValue({ data: {} })
    api.get.mockResolvedValue({ data: { entries: [] } })
    const files = [new File(['a'], '.env'), new File(['b'], 'archive.bin')]

    const count = await workspace.uploadFiles('.', files)

    expect(count).toBe(2)
    expect(api.post).toHaveBeenCalledTimes(2)
    expect(api.post.mock.calls[0][0]).toBe('/workspace/files')
    expect(api.post.mock.calls[0][2].params).toEqual({
      parent: '.',
      scope: 'session',
      session_id: 'session-a'
    })
    expect(api.get).toHaveBeenCalledWith('/workspace/entries', {
      params: { path: '.', scope: 'session', session_id: 'session-a' },
      suppressErrorToast: true
    })
  })

  it('does not use a context-free draft current directory', async () => {
    const workspace = useWorkspaceStore()
    workspace.configure({ currentDirectory: '/draft-project' })

    expect(await workspace.load('.')).toBe(false)
    workspace.returnToCurrentContext()

    expect(api.get).not.toHaveBeenCalled()
  })

  it('routes a draft computer browser through its selected agent and absolute directory', async () => {
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    workspace.configure({
      currentDirectory: '/projekt',
      workspaceContext: {
        executionAgentId: 'agent-1',
        connected: true,
        operations: ['filesystem.list', 'filesystem.read']
      }
    })

    await workspace.load('/projekt')

    expect(api.get).toHaveBeenCalledWith('/workspace/entries', {
      params: {
        path: '/projekt',
        scope: 'filesystem',
        execution_agent_id: 'agent-1',
        current_directory: '/projekt'
      },
      suppressErrorToast: true
    })
    expect(workspace.operationAvailable('filesystem.read')).toBe(true)
    expect(workspace.operationAvailable('filesystem.write')).toBe(false)
  })

  it('navigates absolute parent directories up to the filesystem root', async () => {
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    workspace.configure({
      currentDirectory: '/home/user/project',
      workspaceContext: liveContext(['filesystem.list'])
    })

    await workspace.openDirectory(parentPath(workspace.directoryPath))
    await workspace.openDirectory(parentPath(workspace.directoryPath))
    await workspace.openDirectory(parentPath(workspace.directoryPath))

    expect(workspace.directoryPath).toBe('/')
    expect(parentPath('/')).toBe('/')
    expect(workspace.openDirectory('relative/path')).toBe(false)
    expect(api.get.mock.calls.map(([, config]) => config.params.path)).toEqual(['/home/user', '/home', '/'])
  })

  it('defaults persisted sessions to managed files and clears stale viewer state when switching source', async () => {
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    workspace.configure({
      sessionId: 'session-a',
      workspaceContext: liveContext(['filesystem.list'])
    })
    workspace.openEntry({ kind: 'file', name: 'managed.txt', path: 'managed.txt' }, true)
    workspace.updateEditorDraft('changed', 'original')

    expect(workspace.scope).toBe('session')
    workspace.configure({
      sessionId: 'session-a',
      workspaceContext: liveContext(['filesystem.list']),
      scope: 'filesystem'
    })
    await workspace.load(workspace.directoryPath)

    expect(workspace.scope).toBe('filesystem')
    expect(workspace.viewedEntry).toBeNull()
    expect(workspace.editorActive).toBe(false)
    expect(workspace.editorDirty).toBe(false)
    expect(api.get).toHaveBeenLastCalledWith('/workspace/entries', {
      params: {
        path: '/',
        scope: 'filesystem',
        session_id: 'session-a',
        execution_agent_id: 'agent-1'
      },
      suppressErrorToast: true
    })
  })

  it('fails closed for null, disconnected and implicit operation contexts', () => {
    const workspace = useWorkspaceStore()

    for (const context of [
      null,
      { executionAgentId: 'agent-1', operations: ['filesystem.list'] },
      { executionAgentId: 'agent-1', connected: true }
    ]) {
      workspace.configure({ workspaceContext: context })
      expect(workspace.operationAvailable('filesystem.list')).toBe(false)
    }
    workspace.scope = 'session'
    expect(workspace.operationAvailable('filesystem.list')).toBe(false)
  })

  it('falls back to persisted session files when a live context is reset', () => {
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a', workspaceContext: liveContext(['filesystem.list']) })
    workspace.workspaceContext = null

    workspace.reset()

    expect(workspace.scope).toBe('session')
    expect(workspace.operationAvailable('filesystem.list')).toBe(true)
  })

  it('does not call the workspace API while the selected agent is offline', async () => {
    const workspace = useWorkspaceStore()
    workspace.configure({
      workspaceContext: {
        executionAgentId: 'agent-1',
        connected: false,
        operations: ['filesystem.list']
      }
    })

    expect(await workspace.load('.')).toBe(false)
    expect(api.get).not.toHaveBeenCalled()
  })

  it('keeps import, export and editing capabilities separate', () => {
    const workspace = useWorkspaceStore()
    workspace.configure({
      workspaceContext: {
        executionAgentId: 'agent-1',
        connected: true,
        operations: ['filesystem.export', 'filesystem.import', 'filesystem.list']
      }
    })

    expect(workspace.operationAvailable('filesystem.import')).toBe(true)
    expect(workspace.operationAvailable('filesystem.export')).toBe(true)
    expect(workspace.operationAvailable('filesystem.write')).toBe(false)
  })

  it('builds directory archive URLs from the current workspace identity', () => {
    const workspace = useWorkspaceStore()
    workspace.configure({
      sessionId: 'session-a',
      rootIdentity: 'project-a',
      currentDirectory: '/project',
      workspaceContext: liveContext(['filesystem.stat', 'filesystem.list', 'filesystem.export']),
      scope: 'filesystem'
    })

    const params = new URL(workspace.archiveUrl('/project/current'), 'https://example.test').searchParams

    expect(Object.fromEntries(params)).toEqual({
      scope: 'filesystem',
      session_id: 'session-a',
      execution_agent_id: 'agent-1',
      current_directory: '/project',
      path: '/project/current',
      archive: 'zip'
    })
    workspace.workspaceContext.operations = ['filesystem.list', 'filesystem.export']
    expect(workspace.archiveUrl('/project/current')).toBe('')
  })

  it('builds managed session archive URLs with their root identity', () => {
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a', rootIdentity: 'project-a', scope: 'session' })

    expect(workspace.archiveUrl('reports')).toBe(
      '/api/workspace/files/download?scope=session&session_id=session-a&root_identity=project-a&path=reports&archive=zip'
    )
  })

  it('opens a tool file through the existing read-only explorer context', async () => {
    api.get
      .mockResolvedValueOnce({ data: { entries: [] } })
      .mockResolvedValueOnce({ data: { kind: 'text', content: 'Bericht' } })
    const workspace = useWorkspaceStore()
    workspace.configure({
      sessionId: 'session-a', rootIdentity: 'project-a', currentDirectory: '/current'
    })

    const target = workspace.fileTargetFromAction({
      root: 'workspace',
      executionAgentId: 'original-agent',
      currentDirectory: '/original-project',
      path: '/original-project/reports/result.txt',
      filename: 'result.txt',
      sizeBytes: 7
    })
    workspace.activateFileTab({ tabId: 'file-1', kind: 'file', fileTarget: target })
    await workspace.load(workspace.directoryPath)
    await workspace.preview('/original-project/reports/result.txt')

    expect(workspace.viewedEntry).toEqual({
      kind: 'file', name: 'result.txt', path: '/original-project/reports/result.txt'
    })
    expect(api.get.mock.calls.map(([, config]) => config.params)).toEqual([
      {
        path: '/original-project/reports',
        scope: 'filesystem',
        session_id: 'session-a',
        execution_agent_id: 'original-agent',
        current_directory: '/original-project'
      },
      {
        path: '/original-project/reports/result.txt',
        scope: 'filesystem',
        session_id: 'session-a',
        execution_agent_id: 'original-agent',
        current_directory: '/original-project'
      }
    ])
    expect(workspace.fileUrl('download', '/original-project/reports/result.txt')).not.toContain('root_identity')
    expect(workspace.archiveUrl(workspace.directoryPath)).toBe('')
    await expect(workspace.createDirectory('.', 'blocked')).rejects.toThrow('Historical workspace snapshots are read-only')
    workspace.returnToCurrentContext()
    expect(workspace.rootIdentity).toBe('project-a')
  })

  it('restores a historical workspace file with its snapshot context', () => {
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })

    workspace.restoreView({
      scope: 'filesystem',
      selectedPath: '/project/reports/result.txt',
      selectedKind: 'file',
      viewedFilePath: '/project/reports/result.txt',
      rootIdentity: null,
      executionAgentId: '550e8400-e29b-41d4-a716-446655440001',
      currentDirectory: '/project'
    })

    expect(workspace.viewedEntry?.path).toBe('/project/reports/result.txt')
    expect(workspace.rootIdentity).toBeNull()
    expect(workspace.toolContext).toEqual({
      executionAgentId: '550e8400-e29b-41d4-a716-446655440001',
      currentDirectory: '/project'
    })
  })

  it('persists the live filesystem context when no tool snapshot is active', () => {
    const workspace = useWorkspaceStore()
    workspace.configure({
      currentDirectory: '/project',
      workspaceContext: liveContext(['filesystem.list'])
    })

    expect(persistedFileView(workspace)).toMatchObject({
      scope: 'filesystem',
      executionAgentId: 'agent-1',
      currentDirectory: '/project'
    })
  })

  it('restores a selected directory without opening the viewer', () => {
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })

    workspace.restoreView({
      scope: 'session',
      selectedPath: 'drafts',
      selectedKind: 'directory',
      viewedFilePath: null,
      rootIdentity: null,
      executionAgentId: null,
      currentDirectory: null
    })

    expect(workspace.selectedPath).toBe('drafts')
    expect(workspace.selectedKind).toBe('directory')
    expect(workspace.viewedEntry).toBeNull()
  })

  it('opens an ordinary session file action in the live editable root', async () => {
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })

    const target = workspace.fileTargetFromAction({
      root: 'session',
      rootIdentity: 'session-a',
      path: 'plan.md',
      filename: 'plan.md',
      sizeBytes: 7
    })
    workspace.activateFileTab({ tabId: 'file-1', kind: 'file', fileTarget: target })

    expect(workspace.rootIdentity).toBe('session-a')
    expect(workspace.viewedEntry).toEqual({
      kind: 'file', name: 'plan.md', path: 'plan.md'
    })
    expect(workspace.toolContext).toBeNull()
    expect(workspace.operationAvailable('filesystem.write')).toBe(true)
  })

  it('keeps the historical workspace context for tool-file reads', async () => {
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    workspace.configure({
      sessionId: 'session-a',
      currentDirectory: '/current',
      workspaceContext: { executionAgentId: 'current-agent' }
    })

    const target = workspace.fileTargetFromAction({
      root: 'workspace',
      executionAgentId: 'historical-agent',
      currentDirectory: '/original-project',
      path: '/original-project/model.stl',
      filename: 'model.stl',
      sizeBytes: 84
    })
    workspace.activateFileTab({ tabId: 'file-1', kind: 'file', fileTarget: target })
    await workspace.load(workspace.directoryPath)

    expect(api.get).toHaveBeenCalledWith('/workspace/entries', {
      params: {
        path: '/original-project',
        scope: 'filesystem',
        session_id: 'session-a',
        execution_agent_id: 'historical-agent',
        current_directory: '/original-project'
      },
      suppressErrorToast: true
    })
    expect(workspace.fileUrl('content', '/original-project/model.stl')).toContain('execution_agent_id=historical-agent')
  })

  it('opens a structured approval draft target in the editable session file editor', () => {
    const workspace = useWorkspaceStore()
    workspace.configure({
      sessionId: 'session-a', rootIdentity: 'project-a', currentDirectory: '/current'
    })
    const tab = {
      tabId: 'draft-1',
      kind: 'file',
      fileTarget: {
        scope: 'session', path: 'skill-draft.json', name: 'skill-draft.json',
        rootIdentity: 'project-a', readOnly: false
      }
    }
    const controlCenter = useControlCenterStore()
    controlCenter.tabs = [tab]
    workspace.activateFileTab(tab)
    workspace.startFileTabEdit(tab.tabId)

    expect(workspace.scope).toBe('session')
    expect(workspace.rootIdentity).toBe('project-a')
    expect(workspace.editorActive).toBe(true)
    expect(workspace.viewedEntry).toEqual({
      kind: 'file', name: 'skill-draft.json', path: 'skill-draft.json'
    })
  })

  it('keeps directory data and an inline error until retry succeeds', async () => {
    const error = { response: { data: { error: { code: 'workspace.directoryNotFound' } } } }
    api.get
      .mockRejectedValueOnce(error)
      .mockResolvedValueOnce({ data: { entries: [{ path: 'notes.txt' }], hasMore: false } })
    const workspace = useWorkspaceStore()
    workspace.configure({ workspaceContext: liveContext(['filesystem.list']) })
    workspace.entries['.'] = [{ path: 'stale.txt' }]

    expect(await workspace.load('.')).toBe(false)
    expect(workspace.entries['.']).toEqual([{ path: 'stale.txt' }])
    expect(workspace.loadErrors['.']).toEqual(error)

    expect(await workspace.load('.')).toBe(true)
    expect(workspace.entries['.']).toEqual([{ path: 'notes.txt' }])
    expect(workspace.loadErrors['.']).toBeUndefined()
    expect(api.get).toHaveBeenCalledWith('/workspace/entries', {
      params: {
        path: '.',
        scope: 'filesystem',
        execution_agent_id: 'agent-1'
      },
      suppressErrorToast: true
    })
  })

  it('refreshes cached directories and ignores superseded responses', async () => {
    let resolveStale
    let fileRequests = 0
    const rootEntry = { kind: 'directory', name: 'drafts', path: 'drafts' }
    const freshEntry = { kind: 'file', name: 'live.txt', path: 'drafts/live.txt' }
    api.get.mockImplementation((_url, { params }) => {
      if (params.path === 'drafts' && fileRequests++ === 0) {
        return new Promise((resolve) => { resolveStale = resolve })
      }
      return Promise.resolve({
        data: {
          entries: params.path === 'drafts' ? [freshEntry] : [rootEntry],
          hasMore: false,
          nextOffset: null
        }
      })
    })
    const workspace = useWorkspaceStore()
    workspace.scope = 'session'
    workspace.sessionId = 'session-a'
    workspace.entries = {
      '.': [rootEntry],
      drafts: [{ kind: 'file', name: 'stale.txt', path: 'drafts/stale.txt' }]
    }
    workspace.hasMore.drafts = true
    workspace.nextOffset.drafts = 1000
    const contextVersion = workspace.contextVersion

    const staleLoad = workspace.load('drafts')
    await workspace.refresh()
    resolveStale({ data: { entries: [{ path: 'drafts/stale.txt' }] } })
    await staleLoad

    expect(workspace.contextVersion).toBe(contextVersion)
    expect(workspace.entries.drafts).toEqual([freshEntry])
    expect(workspace.hasMore.drafts).toBe(false)
    expect(workspace.nextOffset.drafts).toBeNull()
    expect(api.get.mock.calls.map(([, config]) => config.params.path)).toEqual([
      'drafts', '.', 'drafts'
    ])
  })

  it('renames an entry and clears its cached subtree', async () => {
    api.patch.mockResolvedValue({
      data: {
        entry: {
          kind: 'directory',
          name: 'renamed',
          path: 'renamed'
        }
      }
    })
    api.get.mockResolvedValue({ data: { entries: [], hasMore: false } })
    const workspace = useWorkspaceStore()
    workspace.configure({ workspaceContext: liveContext(['filesystem.list', 'filesystem.rename']) })
    workspace.entries = {
      '.': [{ kind: 'directory', name: 'old', path: 'old' }],
      old: [{ kind: 'directory', name: 'nested', path: 'old/nested' }],
      'old/nested': [{ kind: 'file', name: 'file.txt', path: 'old/nested/file.txt' }]
    }
    workspace.hasMore = { old: true }
    workspace.nextOffset = { old: 1000 }
    workspace.selectedPath = 'old/nested/file.txt'
    workspace.viewedEntry = { kind: 'file', name: 'file.txt', path: 'old/nested/file.txt' }

    const renamed = await workspace.renameEntry('old', 'renamed')

    expect(renamed.path).toBe('renamed')
    expect(api.patch).toHaveBeenCalledWith(
      '/workspace/entries',
      { path: 'old', name: 'renamed' },
      {
        params: {
          scope: 'filesystem',
          execution_agent_id: 'agent-1'
        }
      }
    )
    expect(workspace.entries.old).toBeUndefined()
    expect(workspace.entries['old/nested']).toBeUndefined()
    expect(workspace.hasMore.old).toBeUndefined()
    expect(workspace.nextOffset.old).toBeUndefined()
    expect(workspace.selectedPath).toBe('renamed/nested/file.txt')
    expect(workspace.viewedEntry.path).toBe('renamed/nested/file.txt')
    expect(api.get).toHaveBeenCalledWith('/workspace/entries', {
      params: {
        path: '.',
        scope: 'filesystem',
        execution_agent_id: 'agent-1'
      },
      suppressErrorToast: true
    })
  })

  it('ignores a stale directory response after renaming', async () => {
    let resolveStale
    api.get
      .mockImplementationOnce(() => new Promise((resolve) => { resolveStale = resolve }))
      .mockResolvedValueOnce({ data: { entries: [{ path: 'renamed.txt' }] } })
    api.patch.mockResolvedValue({
      data: { entry: { kind: 'file', name: 'renamed.txt', path: 'renamed.txt' } }
    })
    const workspace = useWorkspaceStore()
    workspace.configure({ workspaceContext: liveContext(['filesystem.list', 'filesystem.rename']) })

    const staleLoad = workspace.load('.')
    await workspace.renameEntry('old.txt', 'renamed.txt')
    resolveStale({ data: { entries: [{ path: 'old.txt' }] } })
    await staleLoad

    expect(workspace.entries['.']).toEqual([{ path: 'renamed.txt' }])
  })

  it('ignores a stale directory response after deleting', async () => {
    let resolveStale
    api.get
      .mockImplementationOnce(() => new Promise((resolve) => { resolveStale = resolve }))
      .mockResolvedValueOnce({ data: { entries: [] } })
    api.delete.mockResolvedValue({})
    const workspace = useWorkspaceStore()
    workspace.configure({ workspaceContext: liveContext(['filesystem.delete', 'filesystem.list']) })
    workspace.entries.folder = [{ path: 'folder/file.txt' }]
    workspace.viewedEntry = { kind: 'file', path: 'folder/file.txt' }

    const staleLoad = workspace.load('.')
    await workspace.deleteEntry('folder')
    resolveStale({ data: { entries: [{ path: 'folder' }] } })
    await staleLoad

    expect(workspace.entries['.']).toEqual([])
    expect(workspace.entries.folder).toBeUndefined()
    expect(workspace.viewedEntry).toBeNull()
  })

  it('ignores a stale directory error after the workspace context changes', async () => {
    let rejectStale
    api.get.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectStale = reject }))
    const workspace = useWorkspaceStore()
    workspace.configure({ workspaceContext: liveContext(['filesystem.list']) })

    const staleLoad = workspace.load('.')
    workspace.configure({ sessionId: 'session-b', currentDirectory: '/project' })
    rejectStale(new Error('stale request'))
    await staleLoad

    expect(workspace.loadErrors).toEqual({})
    expect(workspace.loading).toEqual({})
  })

  it('adds only the selected missing file suffix', () => {
    expect(withFileSuffix('Notizen', '.txt')).toBe('Notizen.txt')
    expect(withFileSuffix('README.MD', '.md')).toBe('README.MD')
    expect(withFileSuffix('README.md', '.txt')).toBe('README.md.txt')
    expect(withFileSuffix('modell', '.scad')).toBe('modell.scad')
    expect(withFileSuffix('modell.scad', '.scad')).toBe('modell.scad')
  })

  it('keeps the active draft and dirty state in the workspace store', () => {
    const workspace = useWorkspaceStore()

    workspace.startEdit('alt')
    workspace.updateEditorDraft('neu', 'alt')

    expect(workspace.editorDraft).toBe('neu')
    expect(workspace.editorDirty).toBe(true)
    workspace.discardEdit()
    expect(workspace.editorDraft).toBeNull()
    expect(workspace.editorDirty).toBe(false)
  })

  it('keeps unsaved drafts isolated while switching file tabs', () => {
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a', rootIdentity: 'root-a' })
    const fileTab = (tabId, path) => ({
      tabId,
      kind: 'file',
      appId: 'file-viewer',
      fileTarget: {
        scope: 'session',
        path,
        name: path,
        rootIdentity: 'root-a',
        readOnly: false
      }
    })

    workspace.activateFileTab(fileTab('file-a', 'a.md'))
    workspace.filePreviews['a.md'] = { kind: 'markdown', content: 'A', revision: 'revision-a' }
    workspace.startEdit('A')
    workspace.updateEditorDraft('A geändert', 'A')
    const editorState = markRaw({ doc: 'A changed', history: ['A'] })
    workspace.editorSession = {
      state: editorState, scrollTop: 120, scrollLeft: 24, wrap: false,
      line: 3, column: 2, lines: 8, bytes: 64, language: 'markdown'
    }
    const editorSession = workspace.editorSession
    workspace.activateFileTab(fileTab('file-b', 'b.md'))
    expect(workspace.editorSession).toBeNull()
    expect(workspace.fileTabStates['file-a'].editorSession).toBe(editorSession)
    workspace.startEdit('B')
    workspace.updateEditorDraft('B geändert', 'B')
    workspace.editorSession = { state: markRaw({ doc: 'B changed' }) }

    workspace.activateFileTab(fileTab('file-a', 'a.md'))
    expect(workspace.editorDraft).toBe('A geändert')
    expect(workspace.editorSession).toBe(editorSession)
    expect(workspace.editorSession.state).toBe(editorState)
    expect(persistedFileView(workspace)).not.toHaveProperty('editorSession')
    expect(workspace.filePreviews['a.md'].revision).toBe('revision-a')
    expect(workspace.editorDirty).toBe(true)
    expect(workspace.fileTabDirty('file-b')).toBe(true)
    expect(workspace.hasDirtyEditors).toBe(true)

    workspace.discardAllEdits()
    expect(workspace.editorSession).toBeNull()
    expect(workspace.hasDirtyEditors).toBe(false)
    for (const state of Object.values(workspace.fileTabStates)) {
      expect(state.editorSession).toBeNull()
      expect(state.editorDraft).toBeNull()
    }
  })

  it.each(['reset', 'openEntry', 'discardEdit', 'discardFileTab', 'configure'])(
    'clears the editor session on %s',
    (action) => {
      const workspace = useWorkspaceStore()
      expect(workspace.editorSession).toBeNull()
      workspace.configure({ sessionId: 'session-a' })
      workspace.openEntry({ kind: 'file', path: 'a.md' }, true)
      workspace.activeFileTabId = 'file-a'
      workspace.editorSession = { state: markRaw({ doc: 'A' }) }
      const args = {
        openEntry: { kind: 'file', path: 'b.md' },
        discardFileTab: 'file-a',
        configure: { sessionId: 'session-b' }
      }

      workspace[action](args[action])

      expect(workspace.editorSession).toBeNull()
    }
  )

  it('releases a closed inactive tab session without changing the active editor', () => {
    const workspace = useWorkspaceStore()
    workspace.activeFileTabId = 'file-a'
    workspace.editorSession = { state: markRaw({ doc: 'A' }) }
    workspace.stashActiveFileTab()
    workspace.activeFileTabId = 'file-b'
    workspace.editorSession = { state: markRaw({ doc: 'B' }) }
    const activeSession = workspace.editorSession

    workspace.discardFileTab('file-a')

    expect(workspace.fileTabStates['file-a']).toBeUndefined()
    expect(workspace.editorSession).toBe(activeSession)
  })

  it('keeps a dirty editor and marks an external file mutation', () => {
    const workspace = useWorkspaceStore()
    workspace.openEntry({ kind: 'file', name: 'notes.md', path: 'notes.md' }, true)
    workspace.updateEditorDraft('neu', 'alt')
    workspace.editorSession = { state: markRaw({ doc: 'neu' }) }
    const editorSession = workspace.editorSession

    workspace.noteFileMutation('notes.md')

    expect(workspace.editorSession).toBe(editorSession)
    expect(workspace.editorDraft).toBe('neu')
    expect(workspace.editorDirty).toBe(true)
    expect(workspace.editorExternalChange).toBe(true)
    expect(workspace.fileChangeVersions['notes.md']).toBeUndefined()

    workspace.discardEdit()
    expect(workspace.fileChangeVersions['notes.md']).toBe(1)
    expect(workspace.editorExternalChange).toBe(false)
  })

  it('clears a clean active editor before refreshing an externally changed file', () => {
    const workspace = useWorkspaceStore()
    workspace.openEntry({ kind: 'file', name: 'notes.md', path: 'notes.md' }, true)
    workspace.editorDraft = 'stale'
    workspace.editorSession = { state: markRaw({ doc: 'stale' }) }

    workspace.noteFileMutation('notes.md')

    expect(workspace.editorActive).toBe(true)
    expect(workspace.editorDraft).toBeNull()
    expect(workspace.editorSession).toBeNull()
    expect(workspace.fileChangeVersions['notes.md']).toBe(1)
  })

  it('snapshots only a live active file with the server root identity', () => {
    const controlCenter = useControlCenterStore()
    controlCenter.sessionId = 'session-a'
    controlCenter.open = true
    controlCenter.tabs = [{ appId: 'files', pinned: false }]
    controlCenter.activeAppId = 'files'
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a', rootIdentity: 'project-root' })
    workspace.openEntry({ kind: 'file', name: 'notes.md', path: 'notes.md' }, true)
    workspace.filePreviews['notes.md'] = {
      kind: 'markdown', content: 'alt', revision: 'a'.repeat(64)
    }
    workspace.updateEditorDraft('neu', 'alt')

    expect(workspace.activeFileSnapshot()).toEqual({
      root: 'session',
      root_identity: 'project-root',
      path: 'notes.md',
      sha256: 'a'.repeat(64),
      dirty: true
    })

    workspace.scope = 'filesystem'
    workspace.rootIdentity = null
    workspace.workspaceContext = { executionAgentId: 'agent-1' }
    workspace.currentDirectory = '/project'
    workspace.viewedEntry.path = '/project/notes.md'
    workspace.selectedPath = '/project/notes.md'
    workspace.filePreviews['/project/notes.md'] = workspace.filePreviews['notes.md']
    expect(workspace.activeFileSnapshot()).toEqual({
      root: 'workspace',
      execution_agent_id: 'agent-1',
      path: '/project/notes.md',
      sha256: 'a'.repeat(64),
      dirty: true
    })

    workspace.toolContext = { executionAgentId: 'agent', currentDirectory: '/project' }
    expect(workspace.activeFileSnapshot()).toBeNull()
  })

  it('does not attach a computer file from a different selected computer', () => {
    const workspace = useWorkspaceStore()
    const controlCenter = useControlCenterStore()
    workspace.configure({
      sessionId: 'session-1',
      currentDirectory: '/work',
      workspaceContext: {
        executionAgentId: 'agent-old',
        connected: true,
        operations: ['filesystem.read']
      },
      scope: 'filesystem'
    })
    workspace.openEntry({ kind: 'file', name: 'plan.md', path: '/work/plan.md' })
    workspace.filePreviews['/work/plan.md'] = { revision: 'a'.repeat(64) }
    controlCenter.open = true
    controlCenter.activeAppId = 'files'

    expect(workspace.activeFileSnapshot('agent-new')).toBeNull()
    expect(workspace.activeFileSnapshot(null)).toBeNull()
  })

  it('activates a revision and preserves historical tabs', async () => {
    const workspace = useWorkspaceStore()
    workspace.sessionId = 'session-a'
    workspace.scope = 'session'
    workspace.rootIdentity = 'project-root'
    workspace.openEntry({ kind: 'file', name: 'notes.md', path: 'notes.md' })
    api.get.mockImplementation((url) => Promise.resolve({
      data: url.includes('/revisions/1/preview')
        ? { kind: 'markdown', content: 'alt', revision: 'a'.repeat(64) }
        : url.endsWith('/preview')
          ? { kind: 'markdown', content: 'neu', revision: 'b'.repeat(64) }
          : { fileId: 'file-1', items: [{ revision: 2, current: true, sha256: 'b'.repeat(64) }] }
    }))
    api.post.mockResolvedValue({ data: { currentRevision: 1 } })
    const controlCenter = useControlCenterStore()
    controlCenter.tabs = [1, 2, 3].map((revision) => ({
      tabId: `history-${revision}`, kind: 'file',
      fileTarget: { scope: 'session', rootIdentity: 'project-root', fileId: 'file-1', revision }
    }))
    const close = vi.spyOn(controlCenter, 'closeFile').mockResolvedValue()
    workspace.fileTabStates['history-3'] = { editorDirty: false }

    await workspace.loadRevisions('notes.md')
    await workspace.revisionPreview('notes.md', 1)
    await workspace.activateRevision('notes.md', 1)

    const params = { path: 'notes.md', session_id: 'session-a', scope: 'session', root_identity: 'project-root' }
    expect(api.get).toHaveBeenCalledWith('/workspace/files/revisions/1/preview', {
      params
    })
    expect(api.post).toHaveBeenCalledWith('/workspace/files/revisions/1/activate', {
      path: 'notes.md',
      file_id: 'file-1',
      expected_current_revision: 2,
      expected_current_sha256: 'b'.repeat(64)
    }, {
      params: { session_id: 'session-a', scope: 'session', root_identity: 'project-root' }
    })
    expect(workspace.fileRevisions['notes.md'].items[0].revision).toBe(2)
    expect(workspace.filePreviews['notes.md'].revision).toBe('b'.repeat(64))
    expect(close).not.toHaveBeenCalled()
    expect(workspace.fileTabStates['history-3']).toEqual({ editorDirty: false })
  })

  it('creates an empty UTF-8 file and opens the returned entry for editing', async () => {
    const entry = { kind: 'file', name: 'Notizen.txt', path: 'Notizen.txt', size: 0 }
    api.post.mockResolvedValue({ data: { entry } })
    api.get.mockResolvedValue({ data: { entries: [entry], hasMore: false } })
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })
    vi.clearAllMocks()
    api.post.mockResolvedValue({ data: { entry } })
    api.get.mockResolvedValue({ data: { entries: [entry], hasMore: false } })

    await workspace.createFile('.', 'Notizen.txt')

    const body = api.post.mock.calls[0][1]
    expect(api.post).toHaveBeenCalledWith('/workspace/files', body, {
      params: { parent: '.', scope: 'session', session_id: 'session-a' }
    })
    expect(body.get('file')).toBeInstanceOf(File)
    expect(body.get('file').name).toBe('Notizen.txt')
    expect(workspace.viewedEntry).toEqual(entry)
    expect(workspace.editorActive).toBe(true)
  })

  it('saves with the expected revision and ignores an older preview response', async () => {
    let resolvePreview
    api.get.mockImplementation(() => new Promise((resolve) => { resolvePreview = resolve }))
    api.put.mockResolvedValue({
      data: {
        revision: 'revision-2'
      }
    })
    const workspace = useWorkspaceStore()
    workspace.configure({ workspaceContext: liveContext(['filesystem.read', 'filesystem.write']) })
    workspace.entries = { '.': [{ kind: 'file', name: 'notes.md', path: 'notes.md', size: 3 }] }
    workspace.openEntry(workspace.entries['.'][0], true)
    workspace.filePreviews['notes.md'] = {
      kind: 'markdown', content: 'old', revision: 'revision-1'
    }

    const stalePreview = workspace.preview('notes.md')
    await workspace.saveFile('notes.md', '# Neu\n', 'revision-1')
    resolvePreview({ data: { kind: 'markdown', content: 'old', revision: 'revision-1' } })
    await stalePreview

    expect(api.put).toHaveBeenCalledWith('/workspace/files', {
      path: 'notes.md', content: '# Neu\n', expected_revision: 'revision-1'
    }, {
      params: {
        scope: 'filesystem',
        execution_agent_id: 'agent-1'
      },
      suppressErrorToast: true
    })
    expect(workspace.filePreviews['notes.md']).toEqual({
      kind: 'markdown', content: '# Neu\n', revision: 'revision-2'
    })
    expect(workspace.viewedEntry.size).toBe(6)
    expect(api.get).toHaveBeenCalledWith('/workspace/files/preview', {
      params: {
        path: 'notes.md',
        scope: 'filesystem',
        execution_agent_id: 'agent-1'
      },
      suppressErrorToast: true
    })
  })

  it.each(['typed during save', 'sent', 'original', null])(
    'keeps the current draft and session when saving completes with draft %s',
    async (draft) => {
      let resolveSave
      api.put.mockImplementationOnce(() => new Promise((resolve) => { resolveSave = resolve }))
      const workspace = useWorkspaceStore()
      workspace.configure({ sessionId: 'session-a' })
      workspace.openEntry({ kind: 'file', path: 'notes.md' }, true)
      workspace.updateEditorDraft('sent', 'original')
      const request = workspace.saveFile('notes.md', workspace.editorDraft, 'revision-1')
      workspace.updateEditorDraft(draft, 'original')
      workspace.editorSession = { state: markRaw({ doc: draft }) }
      const editorSession = workspace.editorSession
      resolveSave({ data: { revision: 'revision-2' } })

      await expect(request).resolves.toEqual({ revision: 'revision-2' })

      expect(api.put.mock.calls[0][1].content).toBe('sent')
      expect(workspace.filePreviews['notes.md'].content).toBe('sent')
      expect(workspace.editorDraft).toBe(draft)
      expect(workspace.editorSession).toBe(editorSession)
      expect(workspace.editorActive).toBe(true)
      expect(workspace.editorDirty).toBe(draft !== null && draft !== 'sent')
    }
  )

  it.each(['path', 'path round trip', 'tab', 'stash', 'context', 'close', 'direct path'])(
    'ignores save completion after a %s change',
    async (change) => {
      let resolveSave
      api.put.mockImplementationOnce(() => new Promise((resolve) => { resolveSave = resolve }))
      const workspace = useWorkspaceStore()
      workspace.configure({ sessionId: 'session-a' })
      workspace.openEntry({ kind: 'file', path: 'a.md' }, true)
      workspace.activeFileTabId = 'file-a'
      workspace.updateEditorDraft('sent', 'original')
      const request = workspace.saveFile('a.md', 'sent', 'revision-1')
      if (change.startsWith('path')) workspace.openEntry({ kind: 'file', path: 'b.md' }, true)
      if (change === 'path round trip') workspace.openEntry({ kind: 'file', path: 'a.md' }, true)
      if (change === 'tab') workspace.activateFileTab({
        tabId: 'file-b', kind: 'file', fileTarget: { scope: 'session', path: 'a.md', name: 'a.md' }
      })
      if (change === 'stash') workspace.stashActiveFileTab()
      if (change === 'context') workspace.configure({ sessionId: 'session-b' })
      if (change === 'close') workspace.discardFileTab('file-a')
      if (change === 'direct path') workspace.viewedEntry.path = 'b.md'
      workspace.editorDraft = 'current draft'
      workspace.editorDirty = true
      workspace.editorExternalChange = true
      workspace.editorSession = { state: markRaw({ doc: 'current draft' }) }
      const currentState = { ...workspace.$state }
      const currentPreviews = { ...workspace.filePreviews }
      resolveSave({ data: {
        entry: { kind: 'file', path: 'a.md', size: 4 }, revision: 'revision-2'
      } })

      await expect(request).resolves.toBeNull()

      expect(workspace.$state).toEqual(currentState)
      expect(workspace.filePreviews).toEqual(currentPreviews)
      expect(workspace.editorSession).toBe(currentState.editorSession)
    }
  )

  it.each([
    [false, 'notes.md', true],
    [true, 'notes.md', true],
    [true, 'other.md', false],
    [true, null, false]
  ])('preserves external hints only for mutations observed during saving (%s, %s)', async (flag, mutation, expected) => {
    let resolveSave
    api.put.mockImplementationOnce(() => new Promise((resolve) => { resolveSave = resolve }))
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })
    workspace.openEntry({ kind: 'file', path: 'notes.md' }, true)
    workspace.updateEditorDraft('sent', 'original')
    workspace.editorExternalChange = flag
    const request = workspace.saveFile('notes.md', 'sent', 'revision-1')
    if (mutation) workspace.noteFileMutation(mutation)
    resolveSave({ data: { revision: 'revision-2' } })

    await request

    expect(workspace.editorDirty).toBe(false)
    expect(workspace.editorExternalChange).toBe(expected)
  })

  it('requests OpenSCAD rendering with the latest revision and workspace context', async () => {
    const model = new Blob(['off'], { type: 'model/vnd.off' })
    api.post.mockResolvedValue({ data: model, headers: { 'content-type': 'model/vnd.off' } })
    const workspace = useWorkspaceStore()
    workspace.configure({
      currentDirectory: '/project',
      workspaceContext: liveContext(['filesystem.read'])
    })
    const controller = new AbortController()

    await expect(workspace.renderOpenScad('model.scad', 'revision-2', controller.signal)).resolves.toBe(model)

    expect(api.post).toHaveBeenCalledWith('/workspace/files/openscad/render', {
      path: 'model.scad', expectedRevision: 'revision-2', output: 'preview'
    }, {
      params: {
        scope: 'filesystem',
        execution_agent_id: 'agent-1',
        current_directory: '/project'
      },
      responseType: 'blob',
      signal: controller.signal,
      suppressErrorToast: true
    })

    await workspace.renderOpenScad('model.scad', 'revision-2', undefined, 'stl')
    expect(api.post).toHaveBeenLastCalledWith('/workspace/files/openscad/render', {
      path: 'model.scad', expectedRevision: 'revision-2', output: 'stl'
    }, expect.objectContaining({ suppressErrorToast: false }))
  })
})
