import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
  DEFAULT_WORKSPACE_SPLIT_RATIO,
  MAX_WORKSPACE_SPLIT_RATIO,
  MIN_WORKSPACE_SPLIT_RATIO,
  withFileSuffix,
  useWorkspaceStore
} from '../src/stores/workspace'

const originalLocalStorage = globalThis.localStorage

function storage(initialValue = null) {
  let value = initialValue
  return {
    getItem: vi.fn(() => value),
    setItem: vi.fn((_key, nextValue) => { value = nextValue })
  }
}

describe('workspace store', () => {
  beforeEach(() => {
    delete globalThis.localStorage
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => {
    if (originalLocalStorage) globalThis.localStorage = originalLocalStorage
    else delete globalThis.localStorage
  })

  it('loads and persists a validated split ratio', () => {
    globalThis.localStorage = storage('64')
    const workspace = useWorkspaceStore()

    expect(workspace.splitRatio).toBe(64)
    workspace.setSplitRatio(67)
    workspace.setSplitRatio(90)

    expect(workspace.splitRatio).toBe(67)
    expect(globalThis.localStorage.setItem).toHaveBeenCalledOnce()
    expect(globalThis.localStorage.setItem).toHaveBeenCalledWith(
      'matoLoopWorkspaceSplitRatio',
      '67'
    )
  })

  it('falls back when the persisted split ratio is invalid', () => {
    globalThis.localStorage = storage('invalid')

    expect(useWorkspaceStore().splitRatio).toBe(DEFAULT_WORKSPACE_SPLIT_RATIO)
  })

  it('keeps both split panes at least 30 percent wide', () => {
    globalThis.localStorage = storage('30')
    const workspace = useWorkspaceStore()

    expect(MIN_WORKSPACE_SPLIT_RATIO).toBe(30)
    expect(MAX_WORKSPACE_SPLIT_RATIO).toBe(70)
    expect(workspace.splitRatio).toBe(30)
    workspace.setSplitRatio(70)
    workspace.setSplitRatio(29)
    workspace.setSplitRatio(71)

    expect(workspace.splitRatio).toBe(70)
    expect(globalThis.localStorage.setItem).toHaveBeenCalledOnce()
    expect(globalThis.localStorage.setItem).toHaveBeenCalledWith(
      'matoLoopWorkspaceSplitRatio',
      '70'
    )
  })

  it('isolates entries when session or scope changes', async () => {
    api.get
      .mockResolvedValueOnce({ data: { entries: [{ path: 'old.txt' }] } })
      .mockResolvedValueOnce({ data: { entries: [] } })
    const workspace = useWorkspaceStore()

    workspace.configure({ sessionId: 'session-a', workingDirectory: 'project' })
    await workspace.load('.')
    workspace.openEntry({ kind: 'file', path: 'old.txt' })
    await workspace.setScope('session')

    expect(workspace.entries).toEqual({ '.': [] })
    expect(workspace.selectedPath).toBe('.')
    expect(workspace.viewedEntry).toBeNull()
    expect(api.get).toHaveBeenLastCalledWith('/workspace/entries', {
      params: { path: '.', scope: 'session', session_id: 'session-a' }
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
      params: { path: 'files', scope: 'session', session_id: 'session-a' }
    })
  })

  it('sends a draft working directory without a persisted session', async () => {
    api.get.mockResolvedValue({ data: { entries: [] } })
    const workspace = useWorkspaceStore()
    workspace.configure({ workingDirectory: 'draft-project' })

    await workspace.load('.')

    expect(api.get).toHaveBeenCalledWith('/workspace/entries', {
      params: {
        path: '.',
        scope: 'filesystem',
        working_directory: 'draft-project'
      }
    })
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
      { params: { scope: 'filesystem' } }
    )
    expect(workspace.entries.old).toBeUndefined()
    expect(workspace.entries['old/nested']).toBeUndefined()
    expect(workspace.hasMore.old).toBeUndefined()
    expect(workspace.nextOffset.old).toBeUndefined()
    expect(workspace.selectedPath).toBe('renamed/nested/file.txt')
    expect(workspace.viewedEntry.path).toBe('renamed/nested/file.txt')
    expect(api.get).toHaveBeenCalledWith('/workspace/entries', {
      params: { path: '.', scope: 'filesystem' }
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

  it('adds only the selected missing file suffix', () => {
    expect(withFileSuffix('Notizen', '.txt')).toBe('Notizen.txt')
    expect(withFileSuffix('README.MD', '.md')).toBe('README.MD')
    expect(withFileSuffix('README.md', '.txt')).toBe('README.md.txt')
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
    }, { params: { scope: 'filesystem' } })
    expect(workspace.filePreviews['notes.md']).toEqual({
      kind: 'markdown', content: '# Neu\n', revision: 'revision-2'
    })
    expect(workspace.viewedEntry.size).toBe(6)
  })
})
