import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import {
  controlCenterGlobalSectionIds,
  controlCenterIntegrationIds
} from 'src/control-center/manifest'

export const DEFAULT_CONTROL_CENTER_SPLIT_RATIO = 60
export const MIN_CONTROL_CENTER_SPLIT_RATIO = 30
export const MAX_CONTROL_CENTER_SPLIT_RATIO = 70
const SPLIT_STORAGE_KEY = 'matoLoopWorkspaceSplitRatio'

function defaultState() {
  return {
    tabs: [{ appId: 'files', pinned: false }],
    activeAppId: 'files',
    open: false,
    fileView: null
  }
}

function copyState(state) {
  return {
    tabs: state.tabs.map((tab) => ({ ...tab })),
    activeAppId: state.activeAppId,
    open: state.open === true,
    fileView: state.fileView ? { ...state.fileView } : null
  }
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
  const tabs = []
  const legacyOrder = [
    ...(value?.tabs || []).filter((tab) => tab?.pinned === true),
    ...(value?.tabs || []).filter((tab) => tab?.pinned !== true)
  ]
  for (const tab of legacyOrder) {
    if (!controlCenterIntegrationIds.has(tab?.appId) || seen.has(tab.appId)) continue
    seen.add(tab.appId)
    tabs.push({ appId: tab.appId, pinned: false })
  }
  const activeAppId = tabs.some((tab) => tab.appId === value?.activeAppId)
    ? value.activeAppId
    : tabs[0]?.appId || null
  const fileView = normalizeFileView(value?.fileView)
  return { tabs, activeAppId, open: value?.open === true, fileView }
}

function normalizeFileView(value) {
  if (!['session', 'filesystem'].includes(value?.scope)) return null
  if (typeof value.selectedPath !== 'string' || !value.selectedPath) return null
  if (!['file', 'directory'].includes(value.selectedKind)) return null
  const viewedFilePath = typeof value.viewedFilePath === 'string' && value.viewedFilePath === value.selectedPath
    ? value.viewedFilePath
    : null
  const rootIdentity = typeof value.rootIdentity === 'string' && value.rootIdentity
    ? value.rootIdentity
    : null
  const snapshot = ['executionAgentId', 'rootId', 'rootRevision', 'workingDirectory']
    .map((key) => typeof value[key] === 'string' && value[key] ? value[key] : null)
  if (!rootIdentity && snapshot.some(Boolean)) return null
  if (value.scope === 'session' && snapshot.some(Boolean)) return null
  if (value.scope === 'filesystem' && rootIdentity && snapshot.some((item) => !item)) return null
  return {
    scope: value.scope,
    selectedPath: value.selectedPath,
    selectedKind: value.selectedKind,
    viewedFilePath,
    rootIdentity,
    executionAgentId: snapshot[0],
    rootId: snapshot[1],
    rootRevision: snapshot[2],
    workingDirectory: snapshot[3]
  }
}

export function controlCenterRequestState(state) {
  return {
    tabs: state.tabs.map((tab) => ({ app_id: tab.appId, pinned: tab.pinned })),
    active_app_id: state.activeAppId,
    open: state.open === true,
    file_view: state.fileView
      ? {
          scope: state.fileView.scope,
          selected_path: state.fileView.selectedPath,
          selected_kind: state.fileView.selectedKind,
          viewed_file_path: state.fileView.viewedFilePath,
          root_identity: state.fileView.rootIdentity,
          execution_agent_id: state.fileView.executionAgentId,
          root_id: state.fileView.rootId,
          root_revision: state.fileView.rootRevision,
          working_directory: state.fileView.workingDirectory
        }
      : null
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
    requestedAppId: null
  }),
  getters: {
    activeContentId: (state) => state.activeGlobalSectionId || state.activeAppId
  },
  actions: {
    applyState(value) {
      const state = normalizeState(value)
      this.tabs = state.tabs
      this.activeAppId = state.activeAppId
      this.open = state.open
      this.fileView = state.fileView
    },
    syncSession(session) {
      const sessionId = session?.id || null
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
      if (requestedAppId) void this.openApp(requestedAppId).catch(() => {})
    },
    bindSession(session) {
      const sessionId = session?.id || null
      this.requestGeneration += 1
      this.ready = false
      this.saving = false
      this.pendingState = null
      if (this.sessionId !== sessionId) {
        this.requestedAppId = null
        this.activeGlobalSectionId = null
      }
      this.sessionId = sessionId
      this.confirmedState = normalizeState(session?.controlCenter || defaultState())
      this.applyState(this.confirmedState)
    },
    resetDraft() {
      this.requestGeneration += 1
      this.ready = true
      this.saving = false
      this.pendingState = null
      this.requestedAppId = null
      this.sessionId = null
      this.confirmedState = defaultState()
      this.applyState(this.confirmedState)
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
    async save(value) {
      const next = normalizeState(value)
      if (!this.ready) return null
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
        }
        throw error
      } finally {
        if (this.sessionId === sessionId && this.requestGeneration === generation) {
          this.saving = false
        }
      }
    },
    async openApp(appId) {
      if (controlCenterGlobalSectionIds.has(appId)) {
        this.activeGlobalSectionId = appId
        this.requestedAppId = null
        return this.setOpen(true)
      }
      if (!controlCenterIntegrationIds.has(appId)) return
      this.activeGlobalSectionId = null
      if (!this.ready) {
        this.open = true
        this.requestedAppId = appId
        return null
      }
      const exists = this.tabs.some((tab) => tab.appId === appId)
      return this.save({
        tabs: exists ? this.tabs : [...this.tabs, { appId, pinned: false }],
        activeAppId: appId,
        open: true,
        fileView: this.fileView
      })
    },
    setOpen(open) {
      return this.save({
        tabs: this.tabs,
        activeAppId: this.activeAppId,
        open,
        fileView: this.fileView
      })
    },
    setFileView(fileView) {
      return this.save({
        tabs: this.tabs,
        activeAppId: this.activeAppId,
        open: this.open,
        fileView
      })
    },
    async activate(appId) {
      if (!this.tabs.some((tab) => tab.appId === appId)) return
      this.activeGlobalSectionId = null
      if (appId === this.activeAppId) return
      return this.save({ tabs: this.tabs, activeAppId: appId, open: this.open, fileView: this.fileView })
    },
    async removeIntegration(appId) {
      const index = this.tabs.findIndex((tab) => tab.appId === appId)
      if (index < 0) return
      const tabs = this.tabs.filter((tab) => tab.appId !== appId)
      const activeAppId = this.activeAppId === appId
        ? this.tabs[index + 1]?.appId || this.tabs[index - 1]?.appId || null
        : this.activeAppId
      return this.save({ tabs, activeAppId, open: this.open, fileView: this.fileView })
    },
    async reorderIntegration(appId, targetAppId, after = false) {
      if (appId === targetAppId) return
      const moved = this.tabs.find((tab) => tab.appId === appId)
      const remaining = this.tabs.filter((tab) => tab.appId !== appId)
      const targetIndex = remaining.findIndex((tab) => tab.appId === targetAppId)
      if (!moved || targetIndex < 0) return
      const tabs = [...remaining]
      tabs.splice(targetIndex + (after ? 1 : 0), 0, moved)
      if (tabs.every((tab, index) => tab.appId === this.tabs[index]?.appId)) return
      return this.save({ tabs, activeAppId: this.activeAppId, open: this.open, fileView: this.fileView })
    },
    async moveIntegration(appId, offset) {
      const index = this.tabs.findIndex((tab) => tab.appId === appId)
      const target = this.tabs[index + offset]
      if (index < 0 || !target) return
      return this.reorderIntegration(appId, target.appId, offset > 0)
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
