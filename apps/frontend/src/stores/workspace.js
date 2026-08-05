import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const MAX_WORKSPACE_UPLOADS = 4
export const MAX_WORKSPACE_UPLOAD_BYTES = 64 * 1024 * 1024
export const DEFAULT_WORKSPACE_SPLIT_RATIO = 60
export const MIN_WORKSPACE_SPLIT_RATIO = 30
export const MAX_WORKSPACE_SPLIT_RATIO = 70
const WORKSPACE_SPLIT_STORAGE_KEY = 'matoLoopWorkspaceSplitRatio'

function validSplitRatio(value) {
  const ratio = Number(value)
  return Number.isFinite(ratio)
    && ratio >= MIN_WORKSPACE_SPLIT_RATIO
    && ratio <= MAX_WORKSPACE_SPLIT_RATIO
    ? ratio
    : null
}

function storedSplitRatio() {
  try {
    return validSplitRatio(globalThis.localStorage?.getItem(WORKSPACE_SPLIT_STORAGE_KEY))
      ?? DEFAULT_WORKSPACE_SPLIT_RATIO
  } catch {
    return DEFAULT_WORKSPACE_SPLIT_RATIO
  }
}

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
  if (store.rootIdentity) throw new Error('Tool file contexts are read-only')
}

export function withFileSuffix(name, suffix) {
  const value = String(name || '').trim()
  return value.toLocaleLowerCase().endsWith(suffix) ? value : `${value}${suffix}`
}

export const useWorkspaceStore = defineStore('workspace', {
  state: () => ({
    open: false,
    scope: 'filesystem',
    sessionId: null,
    workingDirectory: null,
    rootIdentity: null,
    entries: {},
    hasMore: {},
    loading: {},
    loadVersions: {},
    loadErrors: {},
    nextOffset: {},
    uploading: false,
    selectedPath: '.',
    viewedEntry: null,
    filePreviews: {},
    previewVersions: {},
    editorActive: false,
    editorDirty: false,
    editorDraft: null,
    filter: '',
    splitRatio: storedSplitRatio(),
    contextVersion: 0
  }),
  actions: {
    show() {
      if (this.rootIdentity) this.reset()
      this.open = true
      this.load('.')
    },
    toggle() {
      if (this.open) this.open = false
      else this.show()
    },
    configure({ sessionId = null, workingDirectory = null } = {}) {
      if (this.scope === 'session' && !sessionId) this.scope = 'filesystem'
      const changed = this.sessionId !== sessionId || this.workingDirectory !== workingDirectory
      this.sessionId = sessionId
      this.workingDirectory = workingDirectory
      if (changed) this.reset()
    },
    setScope(scope) {
      if (scope === 'session' && !this.sessionId) return
      if (scope === this.scope && !this.rootIdentity) return
      this.scope = scope
      this.reset()
      return this.load('.')
    },
    reset() {
      this.contextVersion += 1
      this.entries = {}
      this.hasMore = {}
      this.loading = {}
      this.loadVersions = {}
      this.loadErrors = {}
      this.nextOffset = {}
      this.selectedPath = '.'
      this.viewedEntry = null
      this.filePreviews = {}
      this.previewVersions = {}
      this.editorActive = false
      this.editorDirty = false
      this.editorDraft = null
      this.filter = ''
      this.rootIdentity = null
    },
    openToolFile(action) {
      if (
        !['session', 'workspace'].includes(action?.root)
        || action.sessionId !== this.sessionId
        || !action.rootIdentity
        || !action.path
        || !action.filename
      ) return
      this.scope = action.root === 'session' ? 'session' : 'filesystem'
      this.reset()
      this.rootIdentity = action.rootIdentity
      this.selectedPath = action.path
      this.viewedEntry = {
        kind: 'file',
        name: action.filename,
        path: action.path,
        size: action.sizeBytes
      }
      this.open = true
      return this.load('.')
    },
    openEntry(entry, edit = false) {
      if (entry?.kind !== 'file') return
      this.selectedPath = entry.path
      this.viewedEntry = entry
      this.editorActive = edit
      this.editorDirty = false
      this.editorDraft = null
    },
    closeViewer() {
      this.viewedEntry = null
      this.editorActive = false
      this.editorDirty = false
      this.editorDraft = null
    },
    startEdit(content) {
      this.editorActive = true
      this.editorDirty = false
      this.editorDraft = content
    },
    updateEditorDraft(content, original) {
      this.editorDraft = content
      this.editorDirty = this.editorActive && content !== original
    },
    discardEdit() {
      this.editorActive = false
      this.editorDirty = false
      this.editorDraft = null
    },
    setSplitRatio(value) {
      const ratio = validSplitRatio(value)
      if (ratio === null) return
      this.splitRatio = ratio
      try {
        globalThis.localStorage?.setItem(WORKSPACE_SPLIT_STORAGE_KEY, String(ratio))
      } catch {
        // Browser storage is optional UI persistence.
      }
    },
    requestParams(extra = {}) {
      return {
        scope: this.scope,
        ...(this.sessionId ? { session_id: this.sessionId } : {}),
        ...(!this.sessionId && this.workingDirectory
          ? { working_directory: this.workingDirectory }
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
      const params = new URLSearchParams(this.readRequestParams({ path }))
      return `/api/workspace/files/${action}?${params}`
    },
    async load(path = '.', append = false, force = false) {
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
      await api.post('/workspace/directories', { parent, name }, {
        params: this.requestParams()
      })
      await this.load(parent)
    },
    async deleteEntry(path) {
      requireWritableContext(this)
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
      await this.load(this.selectedPath)
    },
    async renameEntry(path, name) {
      requireWritableContext(this)
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
      if (this.viewedEntry) {
        const viewedPath = renamedPath(this.viewedEntry.path, path, destination)
        this.viewedEntry = viewedPath === destination
          ? data.entry
          : { ...this.viewedEntry, path: viewedPath }
      }
      await this.load(parentPath(path))
      return data.entry
    },
    async uploadFiles(parent, files) {
      requireWritableContext(this)
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
    async saveFile(path, content, expectedRevision) {
      requireWritableContext(this)
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
      return data
    },
    download(path) {
      const anchor = document.createElement('a')
      anchor.href = this.fileUrl('download', path)
      anchor.download = ''
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
    }
  }
})
