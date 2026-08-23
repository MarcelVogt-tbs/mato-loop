import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useControlCenterStore } from 'src/stores/control-center'
import { validRelativePath } from 'src/utils/attachments'

export const MAX_WORKSPACE_UPLOADS = 4
export const MAX_WORKSPACE_UPLOAD_BYTES = 64 * 1024 * 1024
const SHA256_PATTERN = /^[0-9a-f]{64}$/u

function parentPath(path) {
  return path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '.'
}

function renamedPath(current, source, destination) {
  if (current === source) return destination
  return current?.startsWith(`${source}/`)
    ? `${destination}${current.slice(source.length)}`
    : current
}

function clearCachedSubtree(store, path) {
  const cachedPaths = new Set([
    ...Object.keys(store.entries),
    ...Object.keys(store.hasMore),
    ...Object.keys(store.nextOffset),
    ...Object.keys(store.loadErrors)
  ])
  for (const key of cachedPaths) {
    if (key === path || key.startsWith(`${path}/`)) {
      delete store.entries[key]
      delete store.hasMore[key]
      delete store.nextOffset[key]
      delete store.loadErrors[key]
    }
  }
}

function replaceCachedEntry(store, entry) {
  for (const [path, entries] of Object.entries(store.entries)) {
    if (entries.some((item) => item.path === entry.path)) {
      store.entries[path] = entries.map((item) => item.path === entry.path ? entry : item)
    }
  }
}

function requireWritableContext(store) {
  if (store.toolContext) throw new Error('Historical workspace snapshots are read-only')
}

function persistedFileView(store) {
  return {
    scope: store.scope,
    selectedPath: store.selectedPath,
    selectedKind: store.selectedKind,
    viewedFilePath: store.viewedEntry?.path || null,
    rootIdentity: store.rootIdentity,
    executionAgentId: store.toolContext?.executionAgentId || null,
    rootId: store.toolContext?.rootId || null,
    rootRevision: store.toolContext?.rootRevision || null,
    workingDirectory: store.toolContext?.workingDirectory || null
  }
}

export function supportsWorkspaceOperation(context, operation) {
  if (
    !context?.executionAgentId
    || !context.rootId
    || context.connected !== true
  ) return false
  const operations = Array.isArray(context.operations)
    ? context.operations
    : Array.isArray(context.capabilities) ? context.capabilities : null
  return operations?.includes(operation) === true
}

export function workspaceContextKey(context) {
  return JSON.stringify([
    context?.executionAgentId || null,
    context?.rootId || null,
    context?.available,
    context?.connected,
    context?.agentConnected,
    context?.operations || context?.capabilities || null
  ])
}

export function withFileSuffix(name, suffix) {
  const value = String(name || '').trim()
  return value.toLocaleLowerCase().endsWith(suffix) ? value : `${value}${suffix}`
}

export const useWorkspaceStore = defineStore('workspace', {
  state: () => ({
    scope: 'filesystem',
    sessionId: null,
    workingDirectory: null,
    workspaceContext: null,
    rootIdentity: null,
    currentRootIdentity: null,
    toolContext: null,
    entries: {},
    hasMore: {},
    loading: {},
    loadVersions: {},
    loadErrors: {},
    nextOffset: {},
    uploading: false,
    selectedPath: '.',
    selectedKind: 'directory',
    viewedEntry: null,
    filePreviews: {},
    previewVersions: {},
    editorActive: false,
    editorDirty: false,
    editorDraft: null,
    editorExternalChange: false,
    fileChangeVersions: {},
    fileRevisions: {},
    viewVersion: 0,
    filter: '',
    contextVersion: 0
  }),
  getters: {
    operationAvailable: (state) => (operation) => (
      state.toolContext
        ? Boolean(
            state.toolContext?.executionAgentId
            && state.toolContext.rootId
            && state.toolContext.rootRevision
          )
        : state.scope === 'session'
          ? Boolean(state.sessionId)
          : supportsWorkspaceOperation(state.workspaceContext, operation)
    )
  },
  actions: {
    persistView() {
      const controlCenter = useControlCenterStore()
      if (
        !this.sessionId
        || !controlCenter.ready
        || controlCenter.sessionId !== this.sessionId
      ) return
      void controlCenter.setFileView(persistedFileView(this)).catch(() => {})
    },
    restoreView(view) {
      const liveRootIdentity = this.rootIdentity
      this.reset(false)
      if (!view || (view.scope === 'session' && !this.sessionId)) {
        this.rootIdentity = liveRootIdentity
        return
      }
      this.scope = view.scope
      this.rootIdentity = view.scope === 'session' ? liveRootIdentity : view.rootIdentity || null
      this.toolContext = view.scope === 'filesystem' && view.rootIdentity
        ? {
            executionAgentId: view.executionAgentId,
            rootId: view.rootId,
            rootRevision: view.rootRevision,
            workingDirectory: view.workingDirectory
          }
        : null
      this.selectedPath = view.selectedPath
      this.selectedKind = view.selectedKind
      this.viewedEntry = view.viewedFilePath
        ? {
            kind: 'file',
            name: view.viewedFilePath.split('/').at(-1),
            path: view.viewedFilePath
          }
        : null
    },
    returnToCurrentContext() {
      if (this.toolContext) {
        this.reset()
        this.rootIdentity = this.currentRootIdentity
      }
      this.persistView()
      return this.load('.')
    },
    configure({ sessionId = null, rootIdentity = null, workingDirectory = null, workspaceContext = null } = {}) {
      if (!this.toolContext) this.scope = sessionId ? 'session' : 'filesystem'
      const changed = this.sessionId !== sessionId
        || (!this.toolContext && this.rootIdentity !== rootIdentity)
        || this.workingDirectory !== workingDirectory
        || workspaceContextKey(this.workspaceContext) !== workspaceContextKey(workspaceContext)
      this.sessionId = sessionId
      this.workingDirectory = workingDirectory
      this.workspaceContext = workspaceContext
      if (changed) this.reset()
      this.currentRootIdentity = rootIdentity
      if (!this.toolContext) this.rootIdentity = rootIdentity
    },
    setScope(scope) {
      if (scope === 'session' && !this.sessionId) return
      if (scope === this.scope && !this.rootIdentity) return
      this.scope = scope
      this.reset(false)
      this.persistView()
      return this.load('.')
    },
    reset(fallbackToSession = true) {
      this.contextVersion += 1
      this.viewVersion += 1
      this.entries = {}
      this.hasMore = {}
      this.loading = {}
      this.loadVersions = {}
      this.loadErrors = {}
      this.nextOffset = {}
      this.selectedPath = '.'
      this.selectedKind = 'directory'
      this.viewedEntry = null
      this.filePreviews = {}
      this.previewVersions = {}
      this.editorActive = false
      this.editorDirty = false
      this.editorDraft = null
      this.editorExternalChange = false
      this.fileChangeVersions = {}
      this.fileRevisions = {}
      this.filter = ''
      this.rootIdentity = null
      this.toolContext = null
      if (
        fallbackToSession
        && this.scope === 'filesystem'
        && this.sessionId
        && !supportsWorkspaceOperation(this.workspaceContext, 'workspace.list')
      ) this.scope = 'session'
    },
    openToolFile(action) {
      if (
        !['session', 'workspace'].includes(action?.root)
        || action.sessionId !== this.sessionId
        || !action.rootIdentity
        || !action.path
        || !action.filename
        || (action.root === 'workspace' && (
          !action.executionAgentId
          || !action.rootId
          || !action.rootRevision
          || !action.workingDirectory
        ))
      ) return
      if (action.root === 'session') {
        if (this.rootIdentity && this.rootIdentity !== action.rootIdentity) return
        this.scope = 'session'
        this.rootIdentity = action.rootIdentity
        this.toolContext = null
        this.openEntry({
          kind: 'file',
          name: action.filename,
          path: action.path,
          size: action.sizeBytes
        })
        return this.refresh()
      }
      this.reset(false)
      this.scope = 'filesystem'
      this.rootIdentity = action.rootIdentity
      this.toolContext = {
        executionAgentId: action.executionAgentId,
        rootId: action.rootId,
        rootRevision: action.rootRevision,
        workingDirectory: action.workingDirectory
      }
      this.selectedPath = action.path
      this.selectedKind = 'file'
      this.viewedEntry = {
        kind: 'file',
        name: action.filename,
        path: action.path,
        size: action.sizeBytes
      }
      this.persistView()
      return this.load('.')
    },
    openApprovalDraft(action) {
      if (
        action?.root !== 'session'
        || action.sessionId !== this.sessionId
        || !validRelativePath(action.path)
        || !action.filename
      ) return
      const rootIdentity = this.rootIdentity
      this.reset(false)
      this.scope = 'session'
      this.rootIdentity = action.rootIdentity || rootIdentity
      this.openEntry({
        kind: 'file',
        name: action.filename,
        path: action.path,
        size: action.sizeBytes
      }, true)
      return this.load('.')
    },
    openEntry(entry, edit = false) {
      if (entry?.kind !== 'file') return
      this.viewVersion += 1
      this.selectedPath = entry.path
      this.selectedKind = 'file'
      this.viewedEntry = entry
      this.editorActive = edit
      this.editorDirty = false
      this.editorDraft = null
      this.editorExternalChange = false
      this.persistView()
    },
    selectPath(path, kind = 'directory') {
      if (path !== this.selectedPath || kind !== this.selectedKind) this.viewVersion += 1
      this.selectedPath = path
      this.selectedKind = kind
      this.persistView()
    },
    closeViewer() {
      this.viewVersion += 1
      this.viewedEntry = null
      this.editorActive = false
      this.editorDirty = false
      this.editorDraft = null
      this.editorExternalChange = false
      this.persistView()
    },
    startEdit(content) {
      this.editorActive = true
      this.editorDirty = false
      this.editorDraft = content
      this.editorExternalChange = false
    },
    updateEditorDraft(content, original) {
      this.editorDraft = content
      this.editorDirty = this.editorActive && content !== original
    },
    discardEdit() {
      const refreshPath = this.editorExternalChange ? this.viewedEntry?.path : null
      this.editorActive = false
      this.editorDirty = false
      this.editorDraft = null
      this.editorExternalChange = false
      if (refreshPath) {
        this.fileChangeVersions[refreshPath] = (this.fileChangeVersions[refreshPath] || 0) + 1
      }
    },
    noteFileMutation(path) {
      if (this.viewedEntry?.path !== path) return
      if (this.editorDirty) {
        this.editorExternalChange = true
        return
      }
      this.editorDraft = null
      this.fileChangeVersions[path] = (this.fileChangeVersions[path] || 0) + 1
    },
    activeFileSnapshot() {
      const controlCenter = useControlCenterStore()
      const path = this.viewedEntry?.path
      const sha256 = this.filePreviews[path]?.revision
      if (
        !controlCenter.open
        || controlCenter.activeContentId !== 'files'
        || controlCenter.sessionId !== this.sessionId
        || this.toolContext
        || !path
        || this.selectedPath !== path
        || !SHA256_PATTERN.test(sha256 || '')
      ) return null
      const rootIdentity = this.rootIdentity || (
        this.scope === 'filesystem' && this.workspaceContext?.connected === true
          ? this.workspaceContext.rootId
          : null
      )
      if (!rootIdentity) return null
      return {
        root: this.scope === 'session' ? 'session' : 'workspace',
        root_identity: rootIdentity,
        path,
        sha256,
        dirty: this.editorDirty
      }
    },
    requestParams(extra = {}) {
      const liveFilesystemContext = this.scope === 'filesystem'
        && !this.toolContext
        && this.workspaceContext?.executionAgentId
        && this.workspaceContext?.rootId
      return {
        scope: this.scope,
        ...(this.sessionId ? { session_id: this.sessionId } : {}),
        ...(liveFilesystemContext
          ? { execution_agent_id: this.workspaceContext.executionAgentId }
          : {}),
        ...(liveFilesystemContext
          ? { root_id: this.workspaceContext.rootId }
          : {}),
        ...(liveFilesystemContext && this.workingDirectory
          ? { working_directory: this.workingDirectory }
          : {}),
        ...(this.scope === 'filesystem' && this.toolContext
          ? {
              execution_agent_id: this.toolContext.executionAgentId,
              root_id: this.toolContext.rootId,
              root_revision: this.toolContext.rootRevision,
              working_directory: this.toolContext.workingDirectory
            }
          : {}),
        ...extra
      }
    },
    readRequestParams(extra = {}) {
      return {
        ...this.requestParams(),
        ...(this.rootIdentity ? { root_identity: this.rootIdentity } : {}),
        ...extra
      }
    },
    fileUrl(action, path) {
      if (!this.operationAvailable(action === 'download' ? 'workspace.export' : 'workspace.read')) return ''
      const params = new URLSearchParams(this.readRequestParams({ path }))
      return `/api/workspace/files/${action}?${params}`
    },
    async load(path = '.', append = false, force = false) {
      if (!this.operationAvailable('workspace.list')) return false
      if (this.loading[path] && !force) return
      const version = this.contextVersion
      const loadVersion = (this.loadVersions[path] || 0) + 1
      this.loadVersions[path] = loadVersion
      this.loading[path] = true
      try {
        const response = await api.get('/workspace/entries', {
          params: this.readRequestParams({ path, ...(append ? { offset: this.nextOffset[path] } : {}) }),
          suppressErrorToast: true
        })
        if (version === this.contextVersion && loadVersion === this.loadVersions[path]) {
          if (response.data.rootIdentity) this.rootIdentity = response.data.rootIdentity
          this.entries[path] = append
            ? [...(this.entries[path] || []), ...response.data.entries]
            : response.data.entries
          this.hasMore[path] = response.data.hasMore === true
          this.nextOffset[path] = response.data.nextOffset ?? null
          delete this.loadErrors[path]
        }
      } catch (error) {
        if (version === this.contextVersion && loadVersion === this.loadVersions[path]) {
          this.loadErrors[path] = error
        }
        return false
      } finally {
        if (version === this.contextVersion && loadVersion === this.loadVersions[path]) {
          this.loading[path] = false
        }
      }
      return true
    },
    refresh() {
      const paths = new Set(['.', ...Object.keys(this.entries), ...Object.keys(this.loadErrors)])
      return Promise.all([...paths].map((path) => this.load(path, false, true)))
    },
    async createDirectory(parent, name) {
      requireWritableContext(this)
      if (!this.operationAvailable('workspace.mkdir')) throw new Error('Workspace operation is unavailable')
      await api.post('/workspace/directories', { parent, name }, {
        params: this.requestParams()
      })
      await this.load(parent)
    },
    async deleteEntry(path) {
      requireWritableContext(this)
      if (!this.operationAvailable('workspace.delete')) throw new Error('Workspace operation is unavailable')
      const version = this.contextVersion
      await api.delete('/workspace/entries', {
        params: this.requestParams({ path })
      })
      if (version !== this.contextVersion) return
      this.contextVersion += 1
      this.loading = {}
      clearCachedSubtree(this, path)
      if (this.viewedEntry?.path === path || this.viewedEntry?.path.startsWith(`${path}/`)) {
        this.viewedEntry = null
      }
      this.selectedPath = parentPath(path)
      this.selectedKind = 'directory'
      this.persistView()
      await this.load(this.selectedPath)
    },
    async renameEntry(path, name) {
      requireWritableContext(this)
      if (!this.operationAvailable('workspace.rename')) throw new Error('Workspace operation is unavailable')
      const version = this.contextVersion
      const params = this.requestParams()
      const { data } = await api.patch(
        '/workspace/entries',
        { path, name },
        { params }
      )
      if (version !== this.contextVersion) return data.entry
      this.contextVersion += 1
      this.loading = {}
      const destination = data.entry.path
      clearCachedSubtree(this, path)
      this.selectedPath = renamedPath(this.selectedPath, path, destination)
      if (this.selectedPath === destination) this.selectedKind = data.entry.kind
      if (this.viewedEntry) {
        const viewedPath = renamedPath(this.viewedEntry.path, path, destination)
        this.viewedEntry = viewedPath === destination
          ? data.entry
          : { ...this.viewedEntry, path: viewedPath }
      }
      this.persistView()
      await this.load(parentPath(path))
      return data.entry
    },
    async uploadFiles(parent, files) {
      requireWritableContext(this)
      if (!this.operationAvailable('workspace.import')) throw new Error('Workspace operation is unavailable')
      const version = this.contextVersion
      const params = this.requestParams({ parent })
      this.uploading = true
      let uploaded = 0
      try {
        for (const file of files) {
          if (version !== this.contextVersion) {
            const error = new Error('Upload context changed')
            error.uploadInterrupted = true
            throw error
          }
          const body = new FormData()
          body.append('file', file, file.name)
          await api.post('/workspace/files', body, {
            params
          })
          uploaded += 1
          if (version !== this.contextVersion) {
            const error = new Error('Upload context changed')
            error.uploadInterrupted = true
            error.uploadCompleted = uploaded === files.length
            throw error
          }
        }
        return uploaded
      } catch (error) {
        error.uploadedCount = uploaded
        throw error
      } finally {
        if (uploaded && version === this.contextVersion) await this.load(parent)
        this.uploading = false
      }
    },
    async createFile(parent, name) {
      requireWritableContext(this)
      if (!this.operationAvailable('workspace.import')) throw new Error('Workspace operation is unavailable')
      const version = this.contextVersion
      const body = new FormData()
      body.append('file', new File([''], name, { type: 'text/plain;charset=utf-8' }), name)
      const { data } = await api.post('/workspace/files', body, {
        params: this.requestParams({ parent })
      })
      if (version !== this.contextVersion) return data.entry
      this.contextVersion += 1
      this.loading = {}
      delete this.entries[parent]
      this.openEntry(data.entry, true)
      await this.load(parent).catch(() => {})
      return data.entry
    },
    async preview(path) {
      if (!this.operationAvailable('workspace.read')) return null
      const contextVersion = this.contextVersion
      const previewVersion = (this.previewVersions[path] || 0) + 1
      this.previewVersions[path] = previewVersion
      const data = (await api.get('/workspace/files/preview', {
        params: this.readRequestParams({ path }),
        suppressErrorToast: true
      })).data
      if (
        contextVersion !== this.contextVersion
        || previewVersion !== this.previewVersions[path]
      ) return null
      this.filePreviews[path] = data
      return data
    },
    async loadRevisions(path) {
      if (this.scope !== 'session' || this.toolContext || !this.sessionId) return null
      const contextVersion = this.contextVersion
      const { data } = await api.get('/workspace/files/revisions', {
        params: { path, session_id: this.sessionId }
      })
      if (contextVersion !== this.contextVersion || this.viewedEntry?.path !== path) return null
      this.fileRevisions[path] = data
      return data
    },
    async revisionPreview(path, revision) {
      if (this.scope !== 'session' || this.toolContext || !this.sessionId) return null
      return (await api.get(`/workspace/files/revisions/${revision}/preview`, {
        params: { path, session_id: this.sessionId }
      })).data
    },
    revisionDownloadUrl(path, revision) {
      if (this.scope !== 'session' || this.toolContext || !this.sessionId) return ''
      const params = new URLSearchParams({ path, session_id: this.sessionId })
      return `/api/workspace/files/revisions/${revision}/download?${params}`
    },
    async restoreRevision(path, revision) {
      requireWritableContext(this)
      const contextVersion = this.contextVersion
      const viewVersion = this.viewVersion
      const sessionId = this.sessionId
      const state = this.fileRevisions[path] || {}
      const { data } = await api.post(`/workspace/files/revisions/${revision}/restore`, {
        path,
        file_id: state.fileId,
        expected_current_revision: state.items?.find((item) => item.current)?.revision
      }, {
        params: { session_id: sessionId }
      })
      if (
        contextVersion !== this.contextVersion
        || viewVersion !== this.viewVersion
        || sessionId !== this.sessionId
      ) return null
      await Promise.allSettled([
        this.preview(path),
        this.loadRevisions(path),
        this.load(parentPath(path), false, true)
      ])
      if (
        contextVersion !== this.contextVersion
        || viewVersion !== this.viewVersion
        || sessionId !== this.sessionId
      ) return null
      this.fileChangeVersions[path] = (this.fileChangeVersions[path] || 0) + 1
      return data
    },
    async deleteRevision(path, revision) {
      requireWritableContext(this)
      const contextVersion = this.contextVersion
      const viewVersion = this.viewVersion
      const sessionId = this.sessionId
      const state = this.fileRevisions[path] || {}
      const currentRevision = state.items?.find((item) => item.current)?.revision
      const { data } = await api.delete(`/workspace/files/revisions/${revision}`, {
        params: { session_id: sessionId },
        data: {
          path,
          file_id: state.fileId,
          expected_current_revision: currentRevision
        }
      })
      if (
        contextVersion !== this.contextVersion
        || viewVersion !== this.viewVersion
        || sessionId !== this.sessionId
      ) return null
      if (!data.fileDeleted) {
        await this.loadRevisions(path).catch(() => null)
        if (viewVersion !== this.viewVersion) return null
        return data
      }
      delete this.fileRevisions[path]
      delete this.filePreviews[path]
      this.closeViewer()
      this.selectedPath = parentPath(path)
      this.selectedKind = 'directory'
      this.persistView()
      const closedViewVersion = this.viewVersion
      await this.load(this.selectedPath, false, true).catch(() => null)
      if (
        contextVersion !== this.contextVersion
        || closedViewVersion !== this.viewVersion
        || sessionId !== this.sessionId
      ) return null
      return data
    },
    async saveFile(path, content, expectedRevision) {
      requireWritableContext(this)
      if (!this.operationAvailable('workspace.write')) throw new Error('Workspace operation is unavailable')
      const version = this.contextVersion
      const { data } = await api.put('/workspace/files', {
        path,
        content,
        expected_revision: expectedRevision
      }, {
        params: this.requestParams(),
        suppressErrorToast: true
      })
      if (version !== this.contextVersion) return null
      this.contextVersion += 1
      this.loading = {}
      this.previewVersions[path] = (this.previewVersions[path] || 0) + 1
      const previous = this.filePreviews[path] || {}
      this.filePreviews[path] = data.preview || {
        ...previous,
        content,
        revision: data.revision
      }
      const entry = data.entry || (this.viewedEntry?.path === path ? {
        ...this.viewedEntry,
        size: new TextEncoder().encode(content).byteLength,
        modifiedAt: Date.now() / 1000
      } : null)
      if (entry) {
        replaceCachedEntry(this, entry)
        this.viewedEntry = entry
      }
      this.editorDirty = false
      this.editorExternalChange = false
      return data
    },
    async renderOpenScad(path, expectedRevision, signal, output = 'preview') {
      if (!this.operationAvailable('workspace.read')) return null
      const { data, headers } = await api.post('/workspace/files/openscad/render', {
        path,
        expectedRevision,
        output
      }, {
        params: this.readRequestParams(),
        responseType: 'blob',
        signal,
        suppressErrorToast: output === 'preview'
      })
      return data instanceof Blob
        ? data
        : new Blob([data], { type: headers?.['content-type'] || (output === 'stl' ? 'model/stl' : 'model/vnd.off') })
    },
    download(path) {
      if (!this.operationAvailable('workspace.export')) return
      const anchor = document.createElement('a')
      anchor.href = this.fileUrl('download', path)
      anchor.download = ''
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
    }
  }
})
