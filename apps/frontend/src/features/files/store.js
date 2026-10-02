import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useControlCenterStore } from 'src/stores/control-center'
import { safeFileDownloadUrl } from 'src/features/files/attachments'
import {
  clearCachedSubtree,
  parentPath,
  persistedFileView,
  renamedPath,
  replaceCachedEntry,
  requireWritableContext,
  supportsWorkspaceOperation,
  workspaceContextKey
} from 'src/features/files/state'

const SHA256_PATTERN = /^[0-9a-f]{64}$/u

export const useWorkspaceStore = defineStore('workspace', {
  state: () => ({
    scope: 'session',
    sessionId: null,
    currentDirectory: null,
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
    storageUsage: null,
    storageUsageSessionId: null,
    storageUsageLoading: false,
    storageUsageFailed: false,
    storageUsageVersion: 0,
    selectedPath: '.',
    directoryPath: '.',
    selectedKind: 'directory',
    viewedEntry: null,
    filePreviews: {},
    previewVersions: {},
    editorActive: false,
    editorDirty: false,
    editorDraft: null,
    editorSession: null,
    editorExternalChange: false,
    editorMutationVersion: 0,
    activeFileTabId: null,
    fileTabStates: {},
    fileChangeVersions: {},
    fileRevisions: {},
    viewVersion: 0,
    filter: '',
    contextVersion: 0
  }),
  getters: {
    hasDirtyEditors: (state) => state.editorDirty
      || Object.values(state.fileTabStates).some((value) => value.editorDirty),
    fileTabDirty: (state) => (tabId) => state.activeFileTabId === tabId
      ? state.editorDirty
      : state.fileTabStates[tabId]?.editorDirty === true,
    operationAvailable: (state) => (operation) => (
      state.toolContext
        ? Boolean(
            state.toolContext?.executionAgentId
            && state.toolContext.currentDirectory
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
      this.toolContext = view.scope === 'filesystem' && view.executionAgentId && view.currentDirectory
        ? {
            executionAgentId: view.executionAgentId,
            currentDirectory: view.currentDirectory
          }
        : null
      this.selectedPath = view.selectedPath
      this.directoryPath = view.selectedKind === 'directory' ? view.selectedPath : parentPath(view.selectedPath)
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
      return this.load(this.directoryPath)
    },
    configure({ sessionId = null, rootIdentity = null, currentDirectory = null, workspaceContext = null, scope = null } = {}) {
      const nextScope = scope || (sessionId ? 'session' : 'filesystem')
      const nextRootIdentity = sessionId ? rootIdentity || sessionId : rootIdentity
      const sessionChanged = this.sessionId !== sessionId
      const managedRootChanged = Boolean(sessionId && !this.toolContext && this.rootIdentity !== nextRootIdentity)
      if (sessionChanged || managedRootChanged) this.clearStorageUsage()
      if (sessionChanged) {
        this.activeFileTabId = null
        this.fileTabStates = {}
      }
      if (sessionChanged) this.scope = sessionId ? 'session' : 'filesystem'
      else if (
        !this.toolContext
        && !sessionId
        && !this.workspaceContext
        && workspaceContext?.executionAgentId
        && workspaceContext.connected === true
      ) this.scope = 'filesystem'
      const changed = sessionChanged
        || managedRootChanged
        || this.scope !== nextScope
        || this.currentDirectory !== currentDirectory
        || workspaceContextKey(this.workspaceContext) !== workspaceContextKey(workspaceContext)
      this.sessionId = sessionId
      this.scope = nextScope
      this.currentDirectory = currentDirectory
      this.workspaceContext = workspaceContext
      if (changed) this.reset(false)
      this.currentRootIdentity = nextRootIdentity
      if (!this.toolContext) this.rootIdentity = nextRootIdentity
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
      if (
        fallbackToSession
        && this.scope === 'filesystem'
        && this.sessionId
        && !supportsWorkspaceOperation(this.workspaceContext, 'filesystem.list')
      ) this.scope = 'session'
      this.selectedPath = this.scope === 'filesystem' ? this.currentDirectory || '/' : '.'
      this.directoryPath = this.selectedPath
      this.selectedKind = 'directory'
      this.viewedEntry = null
      this.filePreviews = {}
      this.previewVersions = {}
      this.editorActive = false
      this.editorDirty = false
      this.editorDraft = null
      this.editorSession = null
      this.editorExternalChange = false
      this.fileChangeVersions = {}
      this.fileRevisions = {}
      this.filter = ''
      this.rootIdentity = null
      this.toolContext = null
    },
    async resolveFileTarget(action) {
      if (action?.operation === 'provide' && action.targetUrl) {
        const downloadUrl = safeFileDownloadUrl(action.downloadUrl)
        if (!downloadUrl || action.targetUrl !== downloadUrl.replace(/download$/, 'target')) return null
        const { data } = await api.get(action.targetUrl.slice(4))
        return this.fileTargetFromAction({ ...data, revision: action.revision })
      }
      return this.fileTargetFromAction(action)
    },
    fileTargetFromAction(action) {
      if (
        !['session', 'workspace'].includes(action?.root)
        || !action.path
        || !action.filename
        || (action.kind && action.kind !== 'file')
        || action.operation === 'delete'
      ) return null
      if (action.root === 'session') {
        if (!action.rootIdentity) return null
        return {
          scope: 'session',
          path: action.path,
          name: action.filename,
          rootIdentity: action.rootIdentity,
          readOnly: action.revision != null,
          ...(action.revision != null ? { revision: action.revision, fileId: action.fileId } : {})
        }
      }
      if (!action.executionAgentId || !action.currentDirectory || !action.path.startsWith('/')) return null
      return {
        scope: 'filesystem',
        path: action.path,
        name: action.filename,
        executionAgentId: action.executionAgentId,
        currentDirectory: action.currentDirectory,
        readOnly: true
      }
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
      this.editorSession = null
      this.editorExternalChange = false
      this.persistView()
    },
    activateFileTab(tab, workspaceContext = null) {
      const target = tab?.fileTarget
      if (tab?.kind !== 'file' || !target) return false
      this.stashActiveFileTab()
      this.reset(false)
      this.activeFileTabId = tab.tabId
      this.scope = target.scope
      this.rootIdentity = target.scope === 'session' ? target.rootIdentity : null
      this.currentDirectory = target.currentDirectory || this.currentDirectory
      this.workspaceContext = workspaceContext || this.workspaceContext
      this.toolContext = target.scope === 'filesystem' && target.readOnly
        ? {
            executionAgentId: target.executionAgentId,
            currentDirectory: target.currentDirectory
          }
        : null
      this.selectedPath = target.path
      this.directoryPath = parentPath(target.path)
      this.selectedKind = 'file'
      this.viewedEntry = {
        kind: 'file', name: target.name, path: target.path,
        ...(target.revision != null ? { revision: target.revision, fileId: target.fileId } : {})
      }
      const saved = this.fileTabStates[tab.tabId]
      if (saved) {
        if (saved.path && saved.path !== target.path) {
          this.rebindFileTabPaths([tab.tabId], saved.path, target.path)
          saved.path = target.path
        }
        this.filePreviews = saved.filePreviews
        this.previewVersions = saved.previewVersions
        this.editorActive = saved.editorActive
        this.editorDirty = saved.editorDirty
        this.editorDraft = saved.editorDraft
        this.editorSession = saved.editorSession
        this.editorExternalChange = saved.editorExternalChange
        this.fileChangeVersions = saved.fileChangeVersions
        this.fileRevisions = saved.fileRevisions
      }
      return true
    },
    stashActiveFileTab(tabId = null) {
      if (!this.activeFileTabId || (tabId && this.activeFileTabId !== tabId)) return
      this.fileTabStates[this.activeFileTabId] = {
        path: this.viewedEntry?.path,
        filePreviews: this.filePreviews,
        previewVersions: this.previewVersions,
        editorActive: this.editorActive,
        editorDirty: this.editorDirty,
        editorDraft: this.editorDraft,
        editorSession: this.editorSession,
        editorExternalChange: this.editorExternalChange,
        fileChangeVersions: this.fileChangeVersions,
        fileRevisions: this.fileRevisions
      }
      this.activeFileTabId = null
    },
    discardFileTab(tabId) {
      if (this.activeFileTabId === tabId) {
        this.discardEdit()
        this.activeFileTabId = null
      }
      delete this.fileTabStates[tabId]
    },
    rebindFileTabPaths(tabIds, source, destination) {
      for (const tabId of tabIds) {
        const state = this.fileTabStates[tabId]
        if (!state) continue
        for (const key of ['filePreviews', 'previewVersions', 'fileChangeVersions', 'fileRevisions']) {
          state[key] = Object.fromEntries(Object.entries(state[key]).map(([path, value]) => [
            renamedPath(path, source, destination),
            value
          ]))
        }
      }
    },
    noteFileTabMutations(tabIds) {
      for (const tabId of tabIds) {
        if (this.activeFileTabId === tabId) {
          this.noteFileMutation(this.viewedEntry?.path)
          continue
        }
        const state = this.fileTabStates[tabId]
        if (!state) continue
        if (state.editorDirty) state.editorExternalChange = true
      }
    },
    startFileTabEdit(tabId) {
      if (useControlCenterStore().fileTabs.find((tab) => tab.tabId === tabId)?.fileTarget.revision != null) return
      if (this.activeFileTabId === tabId) {
        this.editorActive = true
        return
      }
      if (this.fileTabStates[tabId]) this.fileTabStates[tabId].editorActive = true
    },
    discardAllEdits() {
      this.discardEdit()
      this.fileTabStates = Object.fromEntries(Object.entries(this.fileTabStates).map(([tabId, value]) => [
        tabId,
        { ...value, editorActive: false, editorDirty: false, editorDraft: null, editorSession: null, editorExternalChange: false }
      ]))
    },
    fileTarget(entry, edit = false) {
      if (entry?.kind !== 'file') return null
      if (this.scope === 'session') {
        if (!this.rootIdentity) return null
        return {
          scope: 'session',
          path: entry.path,
          name: entry.name,
          rootIdentity: this.rootIdentity,
          readOnly: false,
          edit
        }
      }
      const executionAgentId = this.toolContext?.executionAgentId || this.workspaceContext?.executionAgentId
      const currentDirectory = this.toolContext?.currentDirectory || this.currentDirectory
      if (!executionAgentId || !currentDirectory) return null
      return {
        scope: 'filesystem',
        path: entry.path,
        name: entry.name,
        executionAgentId,
        currentDirectory,
        readOnly: Boolean(this.toolContext),
        edit
      }
    },
    selectPath(path, kind = 'directory') {
      if (path !== this.selectedPath || kind !== this.selectedKind) this.viewVersion += 1
      this.selectedPath = path
      this.selectedKind = kind
      this.persistView()
    },
    openDirectory(path) {
      if (typeof path !== 'string' || (this.scope === 'filesystem' && !path.startsWith('/'))) return false
      this.directoryPath = path
      this.selectPath(path)
      return this.load(path)
    },
    startEdit(content) {
      requireWritableContext(this)
      this.editorActive = true
      this.editorDirty = false
      this.editorDraft = content
      this.editorSession = null
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
      this.editorSession = null
      this.editorExternalChange = false
      if (refreshPath) {
        this.fileChangeVersions[refreshPath] = (this.fileChangeVersions[refreshPath] || 0) + 1
      }
    },
    noteFileMutation(path) {
      if (this.viewedEntry?.path !== path) return
      this.editorMutationVersion += 1
      if (this.editorDirty) {
        this.editorExternalChange = true
        return
      }
      this.editorDraft = null
      this.editorSession = null
      this.fileChangeVersions[path] = (this.fileChangeVersions[path] || 0) + 1
    },
    activeFileSnapshot(expectedExecutionAgentId = undefined) {
      const controlCenter = useControlCenterStore()
      const path = this.viewedEntry?.path
      const sha256 = this.filePreviews[path]?.revision
      const activeFileVisible = (
        controlCenter.activeTab?.kind === 'file'
        && controlCenter.activeTab.tabId === this.activeFileTabId
      ) || controlCenter.activeContentId === 'files'
      if (
        !controlCenter.open
        || !activeFileVisible
        || controlCenter.sessionId !== this.sessionId
        || this.toolContext
        || this.viewedEntry?.revision != null
        || !path
        || this.selectedPath !== path
        || !SHA256_PATTERN.test(sha256 || '')
      ) return null
      const rootIdentity = this.scope === 'session' ? this.rootIdentity : null
      if (this.scope === 'session' && !rootIdentity) return null
      if (this.scope === 'session' && rootIdentity !== this.sessionId) return null
      if (
        this.scope === 'filesystem'
        && expectedExecutionAgentId !== undefined
        && this.workspaceContext?.executionAgentId !== expectedExecutionAgentId
      ) return null
      return {
        root: this.scope === 'session' ? 'session' : 'workspace',
        ...(rootIdentity ? { root_identity: rootIdentity } : {}),
        ...(this.scope === 'filesystem' ? {
          execution_agent_id: this.workspaceContext.executionAgentId
        } : {}),
        path,
        sha256,
        dirty: this.editorDirty
      }
    },
    requestParams(extra = {}) {
      const liveFilesystemContext = this.scope === 'filesystem'
        && !this.toolContext
        && this.workspaceContext?.executionAgentId
      return {
        scope: this.scope,
        ...(this.sessionId ? { session_id: this.sessionId } : {}),
        ...(this.scope === 'session' && this.rootIdentity ? { root_identity: this.rootIdentity } : {}),
        ...(liveFilesystemContext
          ? { execution_agent_id: this.workspaceContext.executionAgentId }
          : {}),
        ...(liveFilesystemContext && this.currentDirectory
          ? { current_directory: this.currentDirectory }
          : {}),
        ...(this.scope === 'filesystem' && this.toolContext
          ? {
              execution_agent_id: this.toolContext.executionAgentId,
              current_directory: this.toolContext.currentDirectory
            }
          : {}),
        ...extra
      }
    },
    readRequestParams(extra = {}) {
      return {
        ...this.requestParams(),
        ...extra
      }
    },
    fileUrl(action, path) {
      if (!this.operationAvailable(action === 'download' ? 'filesystem.export' : 'filesystem.read')) return ''
      const params = new URLSearchParams(this.readRequestParams({ path }))
      return `/api/workspace/files/${action}?${params}`
    },
    archiveUrl(path) {
      if (this.toolContext) return ''
      if (!['filesystem.stat', 'filesystem.list', 'filesystem.export'].every(this.operationAvailable)) return ''
      const params = new URLSearchParams(this.readRequestParams({ path, archive: 'zip' }))
      return `/api/workspace/files/download?${params}`
    },
    async load(path = this.directoryPath, append = false, force = false) {
      if (!this.operationAvailable('filesystem.list')) return false
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
      const paths = new Set([this.directoryPath, ...Object.keys(this.entries), ...Object.keys(this.loadErrors)])
      return Promise.all([...paths].map((path) => this.load(path, false, true)))
    },
    clearStorageUsage() {
      this.storageUsageVersion += 1
      this.storageUsage = null
      this.storageUsageSessionId = null
      this.storageUsageLoading = false
      this.storageUsageFailed = false
    },
    async fetchStorageUsage(sessionId = this.sessionId) {
      if (!sessionId) {
        this.clearStorageUsage()
        return null
      }
      if (this.storageUsageSessionId !== sessionId) {
        this.clearStorageUsage()
        this.storageUsageSessionId = sessionId
      }
      const version = ++this.storageUsageVersion
      this.storageUsageLoading = true
      this.storageUsageFailed = false
      try {
        const { data } = await api.get('/workspace/usage', {
          params: { session_id: sessionId },
          suppressErrorToast: true
        })
        if (version !== this.storageUsageVersion || this.storageUsageSessionId !== sessionId) return null
        this.storageUsage = data
        return data
      } catch {
        if (version === this.storageUsageVersion && this.storageUsageSessionId === sessionId) {
          this.storageUsageFailed = true
        }
        return null
      } finally {
        if (version === this.storageUsageVersion && this.storageUsageSessionId === sessionId) {
          this.storageUsageLoading = false
        }
      }
    },
    refreshStorageUsage(sessionId = this.storageUsageSessionId) {
      if (!sessionId || this.storageUsageSessionId !== sessionId) return null
      return this.fetchStorageUsage(sessionId)
    },
    managedStorageSessionId() {
      return this.scope === 'session' && !this.toolContext ? this.rootIdentity || this.sessionId : null
    },
    async createDirectory(parent, name) {
      requireWritableContext(this)
      if (!this.operationAvailable('filesystem.mkdir')) throw new Error('Filesystem operation is unavailable')
      const storageSessionId = this.managedStorageSessionId()
      await api.post('/workspace/directories', { parent, name }, {
        params: this.requestParams()
      })
      await this.load(parent)
      if (storageSessionId) void this.refreshStorageUsage(storageSessionId)
    },
    async deleteEntry(path) {
      requireWritableContext(this)
      if (!this.operationAvailable('filesystem.delete')) throw new Error('Filesystem operation is unavailable')
      const storageSessionId = this.managedStorageSessionId()
      const version = this.contextVersion
      await api.delete('/workspace/entries', {
        params: this.requestParams({ path })
      })
      if (version !== this.contextVersion) return
      this.contextVersion += 1
      this.loading = {}
      clearCachedSubtree(this, path)
      if (this.viewedEntry?.path === path || this.viewedEntry?.path.startsWith(`${path}/`)) {
        this.discardEdit()
        this.viewedEntry = null
      }
      this.selectedPath = parentPath(path)
      this.selectedKind = 'directory'
      this.persistView()
      await this.load(this.selectedPath)
      if (storageSessionId) void this.refreshStorageUsage(storageSessionId)
    },
    async renameEntry(path, name) {
      requireWritableContext(this)
      if (!this.operationAvailable('filesystem.rename')) throw new Error('Filesystem operation is unavailable')
      const storageSessionId = this.managedStorageSessionId()
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
      if (storageSessionId) void this.refreshStorageUsage(storageSessionId)
      return data.entry
    },
    async uploadFiles(parent, files) {
      requireWritableContext(this)
      if (!this.operationAvailable('filesystem.import')) throw new Error('Filesystem operation is unavailable')
      const storageSessionId = this.managedStorageSessionId()
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
        if (uploaded && storageSessionId) void this.refreshStorageUsage(storageSessionId)
        this.uploading = false
      }
    },
    async createFile(parent, name) {
      requireWritableContext(this)
      if (!this.operationAvailable('filesystem.import')) throw new Error('Filesystem operation is unavailable')
      const storageSessionId = this.managedStorageSessionId()
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
      if (storageSessionId) void this.refreshStorageUsage(storageSessionId)
      return data.entry
    },
    async preview(path, { cache = true } = {}) {
      if (!this.operationAvailable('filesystem.read')) return null
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
      if (cache) this.filePreviews[path] = data
      return data
    },
    async loadRevisions(path) {
      if (this.scope !== 'session' || this.toolContext || !this.sessionId) return null
      const contextVersion = this.contextVersion
      const { data } = await api.get('/workspace/files/revisions', {
        params: this.readRequestParams({ path })
      })
      if (contextVersion !== this.contextVersion || this.viewedEntry?.path !== path) return null
      this.fileRevisions[path] = data
      return data
    },
    async revisionPreview(path, revision, fileId = null) {
      if (this.scope !== 'session' || this.toolContext || !this.sessionId) return null
      return (await api.get(`/workspace/files/revisions/${revision}/preview`, {
        params: this.readRequestParams({ path, ...(fileId ? { file_id: fileId } : {}) })
      })).data
    },
    revisionDownloadUrl(path, revision, fileId = null, action = 'download') {
      if (this.scope !== 'session' || this.toolContext || !this.sessionId) return ''
      const params = new URLSearchParams(this.readRequestParams({ path, ...(fileId ? { file_id: fileId } : {}) }))
      return `/api/workspace/files/revisions/${revision}/${action}?${params}`
    },
    async activateRevision(path, revision, state = this.fileRevisions[path] || {}) {
      requireWritableContext(this)
      const storageSessionId = this.managedStorageSessionId()
      const contextVersion = this.contextVersion
      const viewVersion = this.viewVersion
      const sessionId = this.sessionId
      const { data } = await api.post(`/workspace/files/revisions/${revision}/activate`, {
        path,
        file_id: state.fileId,
        expected_current_revision: state.items?.find((item) => item.current)?.revision,
        expected_current_sha256: state.items?.find((item) => item.current)?.sha256
      }, {
        params: this.readRequestParams()
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
      if (storageSessionId) void this.refreshStorageUsage(storageSessionId)
      return data
    },
    async saveFile(path, content, expectedRevision) {
      requireWritableContext(this)
      if (!this.operationAvailable('filesystem.write')) throw new Error('Filesystem operation is unavailable')
      const storageSessionId = this.managedStorageSessionId()
      const version = this.contextVersion
      const viewVersion = this.viewVersion
      const tabId = this.activeFileTabId
      const mutationVersion = this.editorMutationVersion
      const { data } = await api.put('/workspace/files', {
        path,
        content,
        expected_revision: expectedRevision
      }, {
        params: this.requestParams(),
        suppressErrorToast: true
      })
      if (
        version !== this.contextVersion
        || viewVersion !== this.viewVersion
        || tabId !== this.activeFileTabId
        || this.viewedEntry?.path !== path
      ) return null
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
        if (entry.path === path) this.viewedEntry = entry
      }
      this.editorDirty = this.editorActive && this.editorDraft !== null && this.editorDraft !== content
      if (mutationVersion === this.editorMutationVersion) this.editorExternalChange = false
      if (storageSessionId) void this.refreshStorageUsage(storageSessionId)
      return data
    },
    async renderOpenScad(path, expectedRevision, signal, output = 'preview') {
      if (this.viewedEntry?.revision != null) return null
      if (!this.operationAvailable('filesystem.read')) return null
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
      if (!this.operationAvailable('filesystem.export')) return
      const anchor = document.createElement('a')
      anchor.href = this.fileUrl('download', path)
      anchor.download = ''
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
    }
  }
})

export {
  MAX_WORKSPACE_UPLOAD_BYTES,
  MAX_WORKSPACE_UPLOADS,
  parentPath,
  supportsWorkspaceOperation,
  withFileSuffix,
  workspaceContextKey
} from 'src/features/files/state'
