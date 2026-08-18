import { createPinia, setActivePinia } from 'pinia'
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
import { useControlCenterStore } from '../src/stores/control-center'

function liveContext(operations) {
  return {
    executionAgentId: 'agent-1',
    rootId: 'root-1',
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
      rootId: 'root-1',
      connected: true,
      operations: ['workspace.list', 'workspace.read']
    }

    expect(workspaceContextKey({ ...context, operations: [...context.operations] }))
      .toBe(workspaceContextKey(context))
    expect(workspaceContextKey({ ...context, connected: false }))
      .not.toBe(workspaceContextKey(context))
  })

  it('isolates entries when session or scope changes', async () => {
    api.get
      .mockResolvedValueOnce({ data: { entries: [{ path: 'old.txt' }] } })
      .mockResolvedValueOnce({ data: { entries: [] } })
    const workspace = useWorkspaceStore()

    workspace.configure({
      sessionId: 'session-a',
      workingDirectory: 'project',
      workspaceContext: liveContext(['workspace.list'])
    })
    await workspace.load('.')
    workspace.openEntry({ kind: 'file', path: 'old.txt' })
    workspace.filter = 'old'
    await workspace.setScope('session')

    expect(workspace.entries).toEqual({ '.': [] })
    expect(workspace.selectedPath).toBe('.')
    expect(workspace.viewedEntry).toBeNull()
    expect(workspace.filter).toBe('')
    expect(api.get).toHaveBeenLastCalledWith('/workspace/entries', {
      params: { path: '.', scope: 'session', session_id: 'session-a' },
      suppressErrorToast: true
    })
  })

  it('uploads selected files individually and reloads the target', async () => {
    api.post.mockResolvedValue({ data: {} })
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a', workingDirectory: null })
    await workspace.setScope('session')
    vi.clearAllMocks()
    api.post.mockResolvedValue({ data: {} })
    api.get.mockResolvedValue({ data: { entries: [] } })
    const files = [new File(['a'], '.env'), new File(['b'], 'archive.bin')]

    const count = await workspace.uploadFiles('files', files)

    expect(count).toBe(2)
    expect(api.post).toHaveBeenCalledTimes(2)
    expect(api.post.mock.calls[0][0]).toBe('/workspace/files')
    expect(api.post.mock.calls[0][2].params).toEqual({
      parent: 'files',
      scope: 'session',
      session_id: 'session-a'
    })
    expect(api.get).toHaveBeenCalledWith('/workspace/entries', {
      params: { path: 'files', scope: 'session', session_id: 'session-a' },
      suppressErrorToast: true
    })
  })

  it('does not use a context-free draft working directory', async () => {
    const workspace = useWorkspaceStore()
    workspace.configure({ workingDirectory: 'draft-project' })

    expect(await workspace.load('.')).toBe(false)
    workspace.returnToCurrentContext()

    expect(api.get).not.toHaveBeenCalled()
  })

  it('routes a draft workspace through its selected agent and advertised root', async () => {
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    workspace.configure({
      workingDirectory: 'projekt',
      workspaceContext: {
        executionAgentId: 'agent-1',
        rootId: 'root-1',
        connected: true,
        operations: ['workspace.list', 'workspace.read']
      }
    })

    await workspace.load('.')

    expect(api.get).toHaveBeenCalledWith('/workspace/entries', {
      params: {
        path: '.',
        scope: 'filesystem',
        execution_agent_id: 'agent-1',
        root_id: 'root-1',
        working_directory: 'projekt'
      },
      suppressErrorToast: true
    })
    expect(workspace.operationAvailable('workspace.read')).toBe(true)
    expect(workspace.operationAvailable('workspace.write')).toBe(false)
  })

  it('fails closed for null, incomplete, rootless and implicit operation contexts', () => {
    const workspace = useWorkspaceStore()

    for (const context of [
      null,
      { executionAgentId: 'agent-1', rootId: 'root-1', operations: ['workspace.list'] },
      { executionAgentId: 'agent-1', connected: true, operations: ['workspace.list'] },
      { executionAgentId: 'agent-1', rootId: 'root-1', connected: true }
    ]) {
      workspace.configure({ workspaceContext: context })
      expect(workspace.operationAvailable('workspace.list')).toBe(false)
    }
    workspace.scope = 'session'
    expect(workspace.operationAvailable('workspace.list')).toBe(false)
  })

  it('falls back to persisted session files when a live context is reset', () => {
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a', workspaceContext: liveContext(['workspace.list']) })
    workspace.workspaceContext = null

    workspace.reset()

    expect(workspace.scope).toBe('session')
    expect(workspace.operationAvailable('workspace.list')).toBe(true)
  })

  it('does not call the workspace API while the selected agent is offline', async () => {
    const workspace = useWorkspaceStore()
    workspace.configure({
      workspaceContext: {
        executionAgentId: 'agent-1',
        rootId: 'root-1',
        connected: false,
        operations: ['workspace.list']
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
        rootId: 'root-1',
        connected: true,
        operations: ['workspace.export', 'workspace.import', 'workspace.list']
      }
    })

    expect(workspace.operationAvailable('workspace.import')).toBe(true)
    expect(workspace.operationAvailable('workspace.export')).toBe(true)
    expect(workspace.operationAvailable('workspace.write')).toBe(false)
  })

  it('opens a tool file through the existing read-only explorer context', async () => {
    api.get
      .mockResolvedValueOnce({ data: { entries: [] } })
      .mockResolvedValueOnce({ data: { kind: 'text', content: 'Bericht' } })
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a', workingDirectory: 'current' })

    await workspace.openToolFile({
      root: 'workspace',
      sessionId: 'session-a',
      rootIdentity: 'original',
      rootId: 'original',
      rootRevision: 'b'.repeat(64),
      executionAgentId: 'original-agent',
      workingDirectory: 'original-project',
      path: 'reports/result.txt',
      filename: 'result.txt',
      sizeBytes: 7
    })
    await workspace.preview('reports/result.txt')

    expect(workspace.viewedEntry).toEqual({
      kind: 'file', name: 'result.txt', path: 'reports/result.txt', size: 7
    })
    expect(api.get.mock.calls.map(([, config]) => config.params)).toEqual([
      {
        path: '.',
        scope: 'filesystem',
        session_id: 'session-a',
        execution_agent_id: 'original-agent',
        root_id: 'original',
        root_revision: 'b'.repeat(64),
        working_directory: 'original-project',
        root_identity: 'original'
      },
      {
        path: 'reports/result.txt',
        scope: 'filesystem',
        session_id: 'session-a',
        execution_agent_id: 'original-agent',
        root_id: 'original',
        root_revision: 'b'.repeat(64),
        working_directory: 'original-project',
        root_identity: 'original'
      }
    ])
    expect(workspace.fileUrl('download', 'reports/result.txt')).toContain('root_identity=original')
    await expect(workspace.createDirectory('.', 'blocked')).rejects.toThrow('Tool file contexts are read-only')
    workspace.returnToCurrentContext()
    expect(workspace.rootIdentity).toBeNull()
  })

  it('tracks viewer changes independently from directory refreshes', async () => {
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    const initialVersion = workspace.viewVersion

    workspace.openEntry({ kind: 'file', name: 'one.txt', path: 'one.txt' })
    const openedVersion = workspace.viewVersion
    await workspace.refresh()
    workspace.closeViewer()

    expect(openedVersion).toBe(initialVersion + 1)
    expect(workspace.viewVersion).toBe(openedVersion + 1)
  })

  it('restores a persisted session draft and records closing only the viewer', async () => {
    const path = 'files/drafts/draft-1/plan.md'
    const fileView = {
      scope: 'session',
      selectedPath: path,
      selectedKind: 'file',
      viewedFilePath: path,
      rootIdentity: null,
      executionAgentId: null,
      rootId: null,
      rootRevision: null,
      workingDirectory: null
    }
    const controlCenter = useControlCenterStore()
    controlCenter.syncSession({
      id: 'session-a',
      controlCenter: {
        tabs: [{ appId: 'files', pinned: false }],
        activeAppId: 'files',
        open: true,
        fileView
      }
    })
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })

    workspace.restoreView(controlCenter.fileView)

    expect(workspace.scope).toBe('session')
    expect(workspace.selectedPath).toBe(path)
    expect(workspace.selectedKind).toBe('file')
    expect(workspace.viewedEntry).toEqual({ kind: 'file', name: 'plan.md', path })

    api.patch.mockResolvedValue({
      data: {
        controlCenter: {
          tabs: [{ appId: 'files', pinned: false }],
          activeAppId: 'files',
          open: true,
          fileView: { ...fileView, viewedFilePath: null }
        }
      }
    })
    workspace.closeViewer()

    await vi.waitFor(() => expect(api.patch).toHaveBeenCalledWith('/sessions/session-a', {
      control_center: expect.objectContaining({
        open: true,
        file_view: expect.objectContaining({
          selected_path: path,
          viewed_file_path: null
        })
      })
    }))
  })

  it('restores a historical workspace file with its snapshot context', () => {
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })

    workspace.restoreView({
      scope: 'filesystem',
      selectedPath: 'reports/result.txt',
      selectedKind: 'file',
      viewedFilePath: 'reports/result.txt',
      rootIdentity: '550e8400-e29b-41d4-a716-446655440002',
      executionAgentId: '550e8400-e29b-41d4-a716-446655440001',
      rootId: '550e8400-e29b-41d4-a716-446655440002',
      rootRevision: 'b'.repeat(64),
      workingDirectory: 'project'
    })

    expect(workspace.viewedEntry?.path).toBe('reports/result.txt')
    expect(workspace.rootIdentity).toBe('550e8400-e29b-41d4-a716-446655440002')
    expect(workspace.toolContext).toEqual({
      executionAgentId: '550e8400-e29b-41d4-a716-446655440001',
      rootId: '550e8400-e29b-41d4-a716-446655440002',
      rootRevision: 'b'.repeat(64),
      workingDirectory: 'project'
    })
  })

  it('restores a selected directory without opening the viewer', () => {
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })

    workspace.restoreView({
      scope: 'session',
      selectedPath: 'files/drafts',
      selectedKind: 'directory',
      viewedFilePath: null,
      rootIdentity: null,
      executionAgentId: null,
      rootId: null,
      rootRevision: null,
      workingDirectory: null
    })

    expect(workspace.selectedPath).toBe('files/drafts')
    expect(workspace.selectedKind).toBe('directory')
    expect(workspace.viewedEntry).toBeNull()
  })

  it('opens a session draft in the current editable context', async () => {
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })

    await workspace.openToolFile({
      root: 'session',
      sessionId: 'session-a',
      rootIdentity: 'session-a',
      path: 'files/drafts/draft-1/plan.md',
      filename: 'plan.md',
      sizeBytes: 7,
      intent: 'draft',
      draftId: 'draft-1',
      revision: 1
    })

    expect(workspace.rootIdentity).toBeNull()
    expect(workspace.viewedEntry).toMatchObject({
      path: 'files/drafts/draft-1/plan.md', intent: 'draft', draftId: 'draft-1'
    })
    expect(workspace.operationAvailable('workspace.write')).toBe(true)
  })

  it('keeps the historical workspace context for tool-file reads', async () => {
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    workspace.configure({
      sessionId: 'session-a',
      workingDirectory: 'current',
      workspaceContext: { executionAgentId: 'current-agent', rootId: 'current-root' }
    })

    await workspace.openToolFile({
      root: 'workspace',
      sessionId: 'session-a',
      rootIdentity: 'historical-root',
      rootId: 'historical-root',
      rootRevision: 'a'.repeat(64),
      executionAgentId: 'historical-agent',
      workingDirectory: 'original-project',
      path: 'model.stl',
      filename: 'model.stl',
      sizeBytes: 84
    })

    expect(api.get).toHaveBeenCalledWith('/workspace/entries', {
      params: {
        path: '.',
        scope: 'filesystem',
        session_id: 'session-a',
        execution_agent_id: 'historical-agent',
        root_id: 'historical-root',
        working_directory: 'original-project',
        root_revision: 'a'.repeat(64),
        root_identity: 'historical-root'
      },
      suppressErrorToast: true
    })
    expect(workspace.fileUrl('content', 'model.stl')).toContain('execution_agent_id=historical-agent')
    workspace.returnToCurrentContext()
    expect(workspace.workingDirectory).toBe('current')
    expect(workspace.workspaceContext).toEqual({
      executionAgentId: 'current-agent', rootId: 'current-root'
    })
  })

  it('opens an approval draft in the editable session file editor', async () => {
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a', workingDirectory: 'current' })

    await workspace.openApprovalDraft({
      root: 'session',
      sessionId: 'session-a',
      path: 'files/skill-draft.json',
      filename: 'skill-draft.json',
      sizeBytes: 128,
      initialRevision: 'revision-1'
    })

    expect(workspace.scope).toBe('session')
    expect(workspace.rootIdentity).toBeNull()
    expect(workspace.editorActive).toBe(true)
    expect(workspace.viewedEntry).toEqual({
      kind: 'file', name: 'skill-draft.json', path: 'files/skill-draft.json', size: 128
    })
    expect(api.get).toHaveBeenCalledWith('/workspace/entries', {
      params: { path: '.', scope: 'session', session_id: 'session-a' },
      suppressErrorToast: true
    })
  })

  it('keeps directory data and an inline error until retry succeeds', async () => {
    const error = { response: { data: { error: { code: 'workspace.directoryNotFound' } } } }
    api.get
      .mockRejectedValueOnce(error)
      .mockResolvedValueOnce({ data: { entries: [{ path: 'notes.txt' }], hasMore: false } })
    const workspace = useWorkspaceStore()
    workspace.configure({ workspaceContext: liveContext(['workspace.list']) })
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
        execution_agent_id: 'agent-1',
        root_id: 'root-1'
      },
      suppressErrorToast: true
    })
  })

  it('refreshes cached directories and ignores superseded responses', async () => {
    let resolveStale
    let fileRequests = 0
    const rootEntry = { kind: 'directory', name: 'files', path: 'files' }
    const freshEntry = { kind: 'file', name: 'live.txt', path: 'files/live.txt' }
    api.get.mockImplementation((_url, { params }) => {
      if (params.path === 'files' && fileRequests++ === 0) {
        return new Promise((resolve) => { resolveStale = resolve })
      }
      return Promise.resolve({
        data: {
          entries: params.path === 'files' ? [freshEntry] : [rootEntry],
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
      files: [{ kind: 'file', name: 'stale.txt', path: 'files/stale.txt' }]
    }
    workspace.hasMore.files = true
    workspace.nextOffset.files = 1000
    const contextVersion = workspace.contextVersion

    const staleLoad = workspace.load('files')
    await workspace.refresh()
    resolveStale({ data: { entries: [{ path: 'files/stale.txt' }] } })
    await staleLoad

    expect(workspace.contextVersion).toBe(contextVersion)
    expect(workspace.entries.files).toEqual([freshEntry])
    expect(workspace.hasMore.files).toBe(false)
    expect(workspace.nextOffset.files).toBeNull()
    expect(api.get.mock.calls.map(([, config]) => config.params.path)).toEqual([
      'files', '.', 'files'
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
    workspace.configure({ workspaceContext: liveContext(['workspace.list', 'workspace.rename']) })
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
          execution_agent_id: 'agent-1',
          root_id: 'root-1'
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
        execution_agent_id: 'agent-1',
        root_id: 'root-1'
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
    workspace.configure({ workspaceContext: liveContext(['workspace.list', 'workspace.rename']) })

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
    workspace.configure({ workspaceContext: liveContext(['workspace.delete', 'workspace.list']) })
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
    workspace.configure({ workspaceContext: liveContext(['workspace.list']) })

    const staleLoad = workspace.load('.')
    workspace.configure({ sessionId: 'session-b', workingDirectory: 'project' })
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

  it('creates an empty UTF-8 file and opens the returned entry for editing', async () => {
    const entry = { kind: 'file', name: 'Notizen.txt', path: 'files/Notizen.txt', size: 0 }
    api.post.mockResolvedValue({ data: { entry } })
    api.get.mockResolvedValue({ data: { entries: [entry], hasMore: false } })
    const workspace = useWorkspaceStore()
    workspace.configure({ sessionId: 'session-a' })
    await workspace.setScope('session')
    vi.clearAllMocks()
    api.post.mockResolvedValue({ data: { entry } })
    api.get.mockResolvedValue({ data: { entries: [entry], hasMore: false } })

    await workspace.createFile('files', 'Notizen.txt')

    const body = api.post.mock.calls[0][1]
    expect(api.post).toHaveBeenCalledWith('/workspace/files', body, {
      params: { parent: 'files', scope: 'session', session_id: 'session-a' }
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
    workspace.configure({ workspaceContext: liveContext(['workspace.read', 'workspace.write']) })
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
        execution_agent_id: 'agent-1',
        root_id: 'root-1'
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
        execution_agent_id: 'agent-1',
        root_id: 'root-1'
      },
      suppressErrorToast: true
    })
  })

  it('requests OpenSCAD rendering with the latest revision and workspace context', async () => {
    const model = new Blob(['off'], { type: 'model/vnd.off' })
    api.post.mockResolvedValue({ data: model, headers: { 'content-type': 'model/vnd.off' } })
    const workspace = useWorkspaceStore()
    workspace.configure({
      workingDirectory: 'project',
      workspaceContext: liveContext(['workspace.read'])
    })
    const controller = new AbortController()

    await expect(workspace.renderOpenScad('model.scad', 'revision-2', controller.signal)).resolves.toBe(model)

    expect(api.post).toHaveBeenCalledWith('/workspace/files/openscad/render', {
      path: 'model.scad', expectedRevision: 'revision-2', output: 'preview'
    }, {
      params: {
        scope: 'filesystem',
        execution_agent_id: 'agent-1',
        root_id: 'root-1',
        working_directory: 'project'
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
