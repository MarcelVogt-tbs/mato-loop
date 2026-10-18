import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useChatStore } from 'src/features/chat/store'
import {
  controlCenterFixedAppIds,
  controlCenterGlobalSectionIds,
  controlCenterIntegrationIds
} from 'src/app/control-center/manifest'
import { randomUuid } from 'src/utils/random-id'
import { absoluteComputerPath } from 'src/features/files/state'

export const DEFAULT_CONTROL_CENTER_SPLIT_RATIO = 60
export const MIN_CONTROL_CENTER_SPLIT_RATIO = 30
export const MAX_CONTROL_CENTER_SPLIT_RATIO = 70
const SPLIT_STORAGE_KEY = 'matoLoopWorkspaceSplitRatio'

function defaultState() {
  return {
    tabs: [appTab('session'), appTab('files')],
    activeAppId: 'session',
    open: false,
    fileView: null,
    fileViews: { session: null, filesystem: null }
  }
}

function copyState(state) {
  return {
    tabs: state.tabs.map((tab) => ({
      ...tab,
      ...(tab.fileTarget ? { fileTarget: { ...tab.fileTarget } } : {})
    })),
    activeAppId: state.activeAppId,
    open: state.open === true,
    fileView: state.fileView ? { ...state.fileView } : null,
    fileViews: {
      session: state.fileViews?.session ? { ...state.fileViews.session } : null,
      filesystem: state.fileViews?.filesystem ? { ...state.fileViews.filesystem } : null
    }
  }
}

function appTab(appId) {
  return { tabId: appId, kind: 'app', appId, pinned: false }
}

function normalizedAbsolutePath(value) {
  return absoluteComputerPath(value)
    && !value.includes('\\')
    && ![...value].some((character) => {
      const code = character.charCodeAt(0)
      return code < 32 || (code >= 127 && code <= 159)
    })
    && (value === '/' || /^[A-Z]:\/$/u.test(value) || !value.split('/').slice(1).some((part) => ['', '.', '..'].includes(part)))
}

function normalizedFileTarget(value) {
  if (!['session', 'filesystem'].includes(value?.scope)) return null
  if (typeof value.path !== 'string' || !value.path) return null
  const name = typeof value.name === 'string' && value.name
    ? value.name
    : value.path.split('/').filter(Boolean).at(-1)
  if (!name) return null
  if (value.revision != null && (
    value.scope !== 'session' || !Number.isSafeInteger(value.revision) || value.revision < 1
    || typeof value.fileId !== 'string' || !value.fileId
  )) return null
  if (value.scope === 'session') {
    if (typeof value.rootIdentity !== 'string' || !value.rootIdentity) return null
    return {
      scope: 'session',
      path: value.path,
      name,
      rootIdentity: value.rootIdentity,
      readOnly: value.revision != null || value.readOnly === true,
      ...(value.revision != null ? { fileId: value.fileId, revision: value.revision } : {})
    }
  }
  if (
    !normalizedAbsolutePath(value.path)
    || typeof value.executionAgentId !== 'string'
    || !value.executionAgentId
    || typeof value.currentDirectory !== 'string'
    || !normalizedAbsolutePath(value.currentDirectory)
  ) return null
  return {
    scope: 'filesystem',
    path: value.path,
    name,
    executionAgentId: value.executionAgentId,
    currentDirectory: value.currentDirectory,
    readOnly: false
  }
}

function normalizedTab(value) {
  if (value?.kind === 'file' || value?.appId === 'file-viewer') {
    const target = normalizedFileTarget(value.fileTarget)
    if (
      !target
      || typeof value.tabId !== 'string'
      || !/^file-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u.test(value.tabId)
    ) return null
    return {
      tabId: value.tabId,
      kind: 'file',
      appId: 'file-viewer',
      pinned: false,
      fileTarget: target
    }
  }
  if (!controlCenterIntegrationIds.has(value?.appId)) return null
  const definition = appTab(value.appId)
  return definition.appId === 'file-viewer' ? null : definition
}

function sameFileContext(left, right) {
  return left.scope === right.scope
    && left.rootIdentity === right.rootIdentity
    && left.executionAgentId === right.executionAgentId
    && left.currentDirectory === right.currentDirectory
}

function validSplitRatio(value) {
  const ratio = Number(value)
  return Number.isFinite(ratio)
    && ratio >= MIN_CONTROL_CENTER_SPLIT_RATIO
    && ratio <= MAX_CONTROL_CENTER_SPLIT_RATIO
    ? ratio
    : null
}

function storedSplitRatio() {
  try {
    return validSplitRatio(globalThis.localStorage?.getItem(SPLIT_STORAGE_KEY))
      ?? DEFAULT_CONTROL_CENTER_SPLIT_RATIO
  } catch {
    return DEFAULT_CONTROL_CENTER_SPLIT_RATIO
  }
}

function normalizeState(value) {
  const seen = new Set()
  const tabs = [...controlCenterFixedAppIds].map((appId) => {
    seen.add(appId)
    return appTab(appId)
  })
  const legacyOrder = [
    ...(value?.tabs || []).filter((tab) => tab?.pinned === true),
    ...(value?.tabs || []).filter((tab) => tab?.pinned !== true)
  ]
  for (const tab of legacyOrder) {
    const normalized = normalizedTab(tab)
    if (!normalized || seen.has(normalized.tabId)) continue
    seen.add(normalized.tabId)
    tabs.push(normalized)
  }
  const requestedActive = value?.activeTabId || value?.activeAppId
  const activeAppId = tabs.some((tab) => tab.tabId === requestedActive)
    ? requestedActive
    : tabs[0]?.tabId || null
  const legacyFileView = normalizeFileView(value?.fileView)
  const fileViews = {
    session: normalizeFileView(value?.fileViews?.session),
    filesystem: normalizeFileView(value?.fileViews?.filesystem)
  }
  if (legacyFileView && !fileViews[legacyFileView.scope]) {
    fileViews[legacyFileView.scope] = legacyFileView
  }
  return { tabs, activeAppId, open: value?.open === true, fileView: legacyFileView, fileViews }
}

function normalizeFileView(value) {
  if (!['session', 'filesystem'].includes(value?.scope)) return null
  if (typeof value.selectedPath !== 'string' || !value.selectedPath) return null
  if (!['file', 'directory'].includes(value.selectedKind)) return null
  const viewedFilePath = typeof value.viewedFilePath === 'string' && value.viewedFilePath === value.selectedPath
    ? value.viewedFilePath
    : null
  const rootIdentity = value.scope === 'session' && typeof value.rootIdentity === 'string' && value.rootIdentity
    ? value.rootIdentity
    : null
  const snapshot = ['executionAgentId', 'currentDirectory']
    .map((key) => typeof value[key] === 'string' && value[key] ? value[key] : null)
  if (value.scope === 'session' && snapshot.some(Boolean)) return null
  if (value.scope === 'filesystem' && snapshot.some((item) => !item)) return null
  return {
    scope: value.scope,
    selectedPath: value.selectedPath,
    selectedKind: value.selectedKind,
    viewedFilePath,
    ...(value.scope === 'session' ? { rootIdentity } : {}),
    executionAgentId: snapshot[0],
    currentDirectory: snapshot[1]
  }
}

export function controlCenterRequestState(state) {
  const serializedFileView = (fileView) => fileView
    ? {
        scope: fileView.scope,
        selected_path: fileView.selectedPath,
        selected_kind: fileView.selectedKind,
        viewed_file_path: fileView.viewedFilePath,
        ...(fileView.scope === 'session' ? { root_identity: fileView.rootIdentity } : {}),
        execution_agent_id: fileView.executionAgentId,
        current_directory: fileView.currentDirectory
      }
    : null
  return {
    tabs: state.tabs.map((tab) => ({
      tab_id: tab.tabId,
      kind: tab.kind,
      app_id: tab.appId,
      pinned: tab.pinned,
      ...(tab.fileTarget
        ? {
            file_target: {
              scope: tab.fileTarget.scope,
              path: tab.fileTarget.path,
              name: tab.fileTarget.name,
              root_identity: tab.fileTarget.rootIdentity,
              execution_agent_id: tab.fileTarget.executionAgentId,
              current_directory: tab.fileTarget.currentDirectory,
              read_only: tab.fileTarget.readOnly === true,
              ...(tab.fileTarget.revision != null ? {
                file_id: tab.fileTarget.fileId,
                revision: tab.fileTarget.revision
              } : {})
            }
          }
        : {})
    })),
    active_app_id: state.activeAppId,
    open: state.open === true,
    file_view: serializedFileView(state.fileView),
    file_views: {
      session: serializedFileView(state.fileViews?.session),
      filesystem: serializedFileView(state.fileViews?.filesystem)
    }
  }
}

export const useControlCenterStore = defineStore('control-center', {
  state: () => ({
    sessionId: null,
    ...defaultState(),
    activeGlobalSectionId: null,
    splitRatio: storedSplitRatio(),
    ready: true,
    saving: false,
    requestGeneration: 0,
    pendingState: null,
    confirmedState: defaultState(),
    requestedAppId: null,
    fileDiffModes: {},
    restoredOpen: false
  }),
  getters: {
    activeContentId: (state) => state.activeGlobalSectionId || state.activeAppId,
    activeTab: (state) => state.tabs.find((tab) => tab.tabId === state.activeAppId) || null,
    fileTabs: (state) => state.tabs.filter((tab) => tab.kind === 'file'),
    fileTabsForTarget: (state) => (value) => {
      const target = normalizedFileTarget(value)
      if (!target) return []
      return state.tabs.filter((tab) => tab.kind === 'file'
        && sameFileContext(tab.fileTarget, target)
        && (tab.fileTarget.path === target.path || tab.fileTarget.path.startsWith(`${target.path}/`)))
    }
  },
  actions: {
    applyState(value) {
      const state = normalizeState(value)
      this.tabs = state.tabs
      this.activeAppId = state.activeAppId
      this.open = state.open
      this.fileView = state.fileView
      this.fileViews = state.fileViews
    },
    syncSession(session) {
      const sessionId = session?.id || null
      if (this.sessionId !== sessionId) this.fileDiffModes = {}
      if (this.sessionId !== sessionId) this.activeGlobalSectionId = null
      const requestedAppId = this.sessionId === sessionId ? this.requestedAppId : null
      this.requestGeneration += 1
      this.ready = true
      this.saving = false
      this.pendingState = null
      this.requestedAppId = null
      this.sessionId = sessionId
      this.confirmedState = normalizeState(session?.controlCenter || defaultState())
      this.applyState(this.confirmedState)
      this.restoredOpen = true
      if (requestedAppId) void this.openApp(requestedAppId).catch(() => {})
    },
    bindSession(session) {
      const sessionId = session?.id || null
      this.requestGeneration += 1
      this.ready = false
      this.saving = false
      this.pendingState = null
      if (this.sessionId !== sessionId) {
        this.fileDiffModes = {}
        this.requestedAppId = null
        this.activeGlobalSectionId = null
      }
      this.sessionId = sessionId
      this.confirmedState = normalizeState(session?.controlCenter || defaultState())
      this.applyState(this.confirmedState)
      this.restoredOpen = true
    },
    resetDraft() {
      this.fileDiffModes = {}
      this.requestGeneration += 1
      this.ready = true
      this.saving = false
      this.pendingState = null
      this.requestedAppId = null
      this.sessionId = null
      this.confirmedState = defaultState()
      this.applyState(this.confirmedState)
      this.restoredOpen = false
    },
    async attachCreatedSession(sessionId, serverState) {
      const latest = copyState(this)
      this.requestGeneration += 1
      this.ready = true
      this.saving = false
      this.pendingState = null
      this.sessionId = sessionId
      this.confirmedState = normalizeState(serverState)
      if (JSON.stringify(latest) === JSON.stringify(this.confirmedState)) {
        this.applyState(this.confirmedState)
        return copyState(this.confirmedState)
      }
      return this.save(latest)
    },
    async save(value, { reveal = false } = {}) {
      const next = normalizeState({
        ...value,
        fileView: Object.hasOwn(value, 'fileView') ? value.fileView : this.fileView,
        fileViews: value.fileViews || this.fileViews
      })
      if (!this.ready) return null
      const restoredOpen = this.restoredOpen
      if (reveal) this.restoredOpen = false
      this.applyState(next)
      if (!this.sessionId) {
        this.confirmedState = copyState(next)
        return copyState(next)
      }
      this.pendingState = copyState(next)
      if (this.saving) return copyState(next)
      const sessionId = this.sessionId
      const generation = this.requestGeneration
      this.saving = true
      try {
        while (
          this.sessionId === sessionId
          && this.requestGeneration === generation
          && this.pendingState
        ) {
          const pending = this.pendingState
          this.pendingState = null
          const { data } = await api.patch(`/sessions/${sessionId}`, {
            control_center: controlCenterRequestState(pending)
          })
          if (this.sessionId !== sessionId || this.requestGeneration !== generation) return null
          this.confirmedState = normalizeState(data.controlCenter)
          if (!this.pendingState) this.applyState(this.confirmedState)
        }
        return copyState(this.confirmedState)
      } catch (error) {
        if (this.sessionId === sessionId && this.requestGeneration === generation) {
          this.pendingState = null
          this.applyState(this.confirmedState)
          this.restoredOpen = restoredOpen
        }
        throw error
      } finally {
        if (this.sessionId === sessionId && this.requestGeneration === generation) {
          this.saving = false
        }
      }
    },
    async openApp(appId) {
      if (appId === 'approvals') appId = 'notifications'
      if (appId === 'file-viewer') return null
      if (controlCenterGlobalSectionIds.has(appId)) {
        this.activeGlobalSectionId = appId
        if (!this.ready) {
          this.open = true
          this.requestedAppId = appId
          this.restoredOpen = false
          return null
        }
        this.requestedAppId = null
        return this.setOpen(true)
      }
      if (!this.sessionId && this.ready && appId !== 'session' && controlCenterIntegrationIds.has(appId)) {
        const session = await useChatStore().createEmptySession({
          tabs: this.tabs.some((tab) => tab.appId === appId) ? this.tabs : [...this.tabs, appTab(appId)],
          activeAppId: appId,
          open: true,
          fileView: this.fileView,
          fileViews: this.fileViews
        })
        if (!session) return null
        if (this.sessionId !== session.id) return null
        if (this.activeAppId === appId && this.open) return copyState(this)
      }
      if (this.tabs.some((tab) => tab.tabId === appId)) {
        this.activeGlobalSectionId = null
        return this.save({ tabs: this.tabs, activeAppId: appId, open: true, fileView: this.fileView }, { reveal: true })
      }
      if (!controlCenterIntegrationIds.has(appId)) return
      this.activeGlobalSectionId = null
      if (!this.ready) {
        this.open = true
        this.requestedAppId = appId
        this.restoredOpen = false
        return null
      }
      const exists = this.tabs.some((tab) => tab.appId === appId)
      return this.save({
        tabs: exists ? this.tabs : [...this.tabs, appTab(appId)],
        activeAppId: appId,
        open: true,
        fileView: this.fileView
      }, { reveal: true })
    },
    ensureApp(appId) {
      if (!controlCenterIntegrationIds.has(appId) || this.tabs.some((tab) => tab.appId === appId)) return
      return this.save({
        tabs: [...this.tabs, appTab(appId)],
        activeAppId: this.activeAppId,
        open: this.open,
        fileView: this.fileView
      })
    },
    setOpen(open) {
      return this.save({
        tabs: this.tabs,
        activeAppId: this.activeAppId,
        open,
        fileView: this.fileView
      }, { reveal: open })
    },
    setFileView(fileView) {
      const normalized = normalizeFileView(fileView)
      const fileViews = {
        ...this.fileViews,
        ...(normalized ? { [normalized.scope]: normalized } : {})
      }
      return this.save({
        tabs: this.tabs,
        activeAppId: this.activeAppId,
        open: this.open,
        fileView: normalized,
        fileViews
      })
    },
    async activate(appId) {
      if (!this.tabs.some((tab) => tab.tabId === appId)) return
      if (!this.sessionId && appId !== 'session') return this.openApp(appId)
      this.activeGlobalSectionId = null
      if (appId === this.activeAppId) return
      return this.save({ tabs: this.tabs, activeAppId: appId, open: this.open, fileView: this.fileView })
    },
    async removeIntegration(appId) {
      if (controlCenterFixedAppIds.has(appId)) return
      const index = this.tabs.findIndex((tab) => tab.tabId === appId && tab.kind === 'app')
      if (index < 0) return
      const tabs = this.tabs.filter((tab) => tab.tabId !== appId)
      const activeAppId = this.activeAppId === appId
        ? this.tabs[index + 1]?.tabId || this.tabs[index - 1]?.tabId || null
        : this.activeAppId
      return this.save({ tabs, activeAppId, open: this.open, fileView: this.fileView })
    },
    async reorderIntegration(appId, targetAppId, after = false) {
      if (appId === targetAppId || controlCenterFixedAppIds.has(appId)) return
      const moved = this.tabs.find((tab) => tab.tabId === appId)
      const remaining = this.tabs.filter((tab) => tab.tabId !== appId)
      const targetIndex = remaining.findIndex((tab) => tab.tabId === targetAppId)
      if (!moved || targetIndex < 0) return
      const tabs = [...remaining]
      tabs.splice(targetIndex + (after ? 1 : 0), 0, moved)
      if (tabs.every((tab, index) => tab.tabId === this.tabs[index]?.tabId)) return
      return this.save({ tabs, activeAppId: this.activeAppId, open: this.open, fileView: this.fileView })
    },
    async moveIntegration(appId, offset) {
      if (controlCenterFixedAppIds.has(appId)) return
      const index = this.tabs.findIndex((tab) => tab.tabId === appId)
      const target = this.tabs[index + offset]
      if (index < 0 || !target) return
      return this.reorderIntegration(appId, target.tabId, offset > 0)
    },
    async openFile(fileTarget, { diff = false } = {}) {
      const target = normalizedFileTarget(fileTarget)
      if (!target) return null
      const sourceKey = JSON.stringify([
        target.scope,
        target.rootIdentity || target.executionAgentId,
        target.currentDirectory || null,
        target.revision != null ? target.fileId : target.path,
        target.revision ?? null
      ])
      const existing = this.tabs.find((tab) => tab.kind === 'file' && JSON.stringify([
        tab.fileTarget.scope,
        tab.fileTarget.rootIdentity || tab.fileTarget.executionAgentId,
        tab.fileTarget.currentDirectory || null,
        tab.fileTarget.revision != null ? tab.fileTarget.fileId : tab.fileTarget.path,
        tab.fileTarget.revision ?? null
      ]) === sourceKey)
      if (!existing && this.tabs.length >= 20) return null
      const tabId = existing?.tabId || `file-${randomUuid()}`
      const previousMode = this.fileDiffModes[tabId]
      this.fileDiffModes[tabId] = diff && target.scope === 'filesystem'
      try {
        if (existing) {
          await this.activate(tabId)
          if (!this.open || this.restoredOpen) await this.setOpen(true)
        } else {
          await this.save({
            tabs: [...this.tabs, { tabId, kind: 'file', appId: 'file-viewer', pinned: false, fileTarget: target }],
            activeAppId: tabId, open: true, fileView: this.fileView
          }, { reveal: true })
        }
        return tabId
      } catch (error) {
        this.fileDiffModes[tabId] = previousMode
        throw error
      }
    },
    async closeFile(tabId) {
      const index = this.tabs.findIndex((tab) => tab.tabId === tabId && tab.kind === 'file')
      if (index < 0) return
      const tabs = this.tabs.filter((tab) => tab.tabId !== tabId)
      const activeAppId = this.activeAppId === tabId
        ? this.tabs[index - 1]?.tabId || this.tabs[index + 1]?.tabId || 'files'
        : this.activeAppId
      await this.save({ tabs, activeAppId, open: this.open, fileView: this.fileView })
      delete this.fileDiffModes[tabId]
    },
    async removeFileTabs(tabIds) {
      const removed = new Set(tabIds)
      const tabs = this.tabs.filter((tab) => !removed.has(tab.tabId))
      if (tabs.length === this.tabs.length) return
      const activeAppId = tabs.some((tab) => tab.tabId === this.activeAppId)
        ? this.activeAppId
        : tabs.find((tab) => tab.appId === 'files')?.tabId || 'session'
      const result = await this.save({ tabs, activeAppId, open: this.open, fileView: this.fileView })
      for (const tabId of removed) delete this.fileDiffModes[tabId]
      return result
    },
    async rebindFileTargets(fileTarget, destination = null, tabIds = null) {
      const target = normalizedFileTarget(fileTarget)
      const destinationTarget = typeof destination === 'string'
        ? { ...target, path: destination }
        : normalizedFileTarget(destination)
      if (!target || (destination !== null && !destinationTarget)) return []
      const selectedIds = tabIds ? new Set(tabIds) : null
      const affected = this.fileTabsForTarget(target).filter((tab) => !selectedIds || selectedIds.has(tab.tabId))
      if (!affected.length) return []
      const affectedIds = new Set(affected.map((tab) => tab.tabId))
      const tabs = destinationTarget === null
        ? this.tabs.filter((tab) => !affectedIds.has(tab.tabId))
        : this.tabs.map((tab) => {
            if (!affectedIds.has(tab.tabId)) return tab
            const path = `${destinationTarget.path}${tab.fileTarget.path.slice(target.path.length)}`
            return {
              ...tab,
              fileTarget: {
                ...destinationTarget,
                ...(tab.fileTarget.revision != null ? {
                  fileId: tab.fileTarget.fileId,
                  revision: tab.fileTarget.revision,
                  readOnly: true
                } : {}),
                path,
                name: path.split('/').filter(Boolean).at(-1)
              }
            }
          })
      const activeAppId = tabs.some((tab) => tab.tabId === this.activeAppId)
        ? this.activeAppId
        : tabs.find((tab) => tab.appId === 'files')?.tabId || 'session'
      await this.save({ tabs, activeAppId, open: this.open, fileView: this.fileView })
      if (destinationTarget === null) {
        for (const tabId of affectedIds) delete this.fileDiffModes[tabId]
      }
      return [...affectedIds]
    },
    setSplitRatio(value) {
      const ratio = validSplitRatio(value)
      if (ratio === null) return
      this.splitRatio = ratio
      try {
        globalThis.localStorage?.setItem(SPLIT_STORAGE_KEY, String(ratio))
      } catch {
        // Resizing remains usable when local storage is unavailable.
      }
    }
  }
})
