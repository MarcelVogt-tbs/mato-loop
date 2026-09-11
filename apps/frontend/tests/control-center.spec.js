import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({ api: { patch: vi.fn() } }))
vi.mock('src/boot/axios', () => ({ api }))

import {
  DEFAULT_CONTROL_CENTER_SPLIT_RATIO,
  MAX_CONTROL_CENTER_SPLIT_RATIO,
  MIN_CONTROL_CENTER_SPLIT_RATIO,
  useControlCenterStore
} from '../src/stores/control-center'
import {
  controlCenterGlobalSectionIds,
  controlCenterIntegrationIds
} from 'src/control-center/manifest'

const originalLocalStorage = globalThis.localStorage
const testIntegrationIds = ['calendar', 'test-app']

function storage(initialValue = null) {
  let value = initialValue
  return {
    getItem: vi.fn(() => value),
    setItem: vi.fn((_key, nextValue) => { value = nextValue })
  }
}

function tab(appId) {
  return { tabId: appId, kind: 'app', appId, pinned: false }
}

function requestTab(appId) {
  return { tab_id: appId, kind: 'app', app_id: appId, pinned: false }
}

function response(tabs, activeAppId) {
  return {
    data: {
      controlCenter: {
        tabs: tabs.some((item) => item.appId === 'session') ? tabs : [tab('session'), ...tabs],
        activeAppId
      }
    }
  }
}

function requestState(appIds, activeAppId, open, fileView = null, fileViews = { session: null, filesystem: null }) {
  return {
    tabs: appIds.map(requestTab),
    active_app_id: activeAppId,
    open,
    file_view: fileView,
    file_views: fileViews
  }
}

const sessionFileView = {
  scope: 'session',
  selectedPath: 'drafts/draft-1/plan.md',
  selectedKind: 'file',
  viewedFilePath: 'drafts/draft-1/plan.md',
  rootIdentity: null,
  executionAgentId: null,
  currentDirectory: null
}

describe('control center store', () => {
  beforeEach(() => {
    delete globalThis.localStorage
    testIntegrationIds.forEach((id) => controlCenterIntegrationIds.add(id))
    setActivePinia(createPinia())
    vi.resetAllMocks()
  })

  afterEach(() => {
    testIntegrationIds.forEach((id) => controlCenterIntegrationIds.delete(id))
    if (originalLocalStorage) globalThis.localStorage = originalLocalStorage
    else delete globalThis.localStorage
  })

  it('starts drafts with the fixed Session app and files integration', () => {
    const store = useControlCenterStore()
    expect(store.tabs).toEqual([tab('session'), tab('files')])
    expect(store.activeAppId).toBe('session')
    expect(store.activeContentId).toBe('session')
    expect(store.open).toBe(false)
    expect(store.fileView).toBeNull()
  })

  it('keeps global sections outside the integration manifest', () => {
    expect([...controlCenterGlobalSectionIds]).toEqual(['notifications'])
    expect(controlCenterIntegrationIds.has('agent-coding')).toBe(true)
    expect(controlCenterIntegrationIds.has('notifications')).toBe(false)
    expect(controlCenterIntegrationIds.has('approvals')).toBe(false)
  })

  it('loads and persists the existing validated split ratio', () => {
    globalThis.localStorage = storage('64')
    const store = useControlCenterStore()

    expect(store.splitRatio).toBe(64)
    store.setSplitRatio(67)
    store.setSplitRatio(90)

    expect(store.splitRatio).toBe(67)
    expect(MIN_CONTROL_CENTER_SPLIT_RATIO).toBe(30)
    expect(MAX_CONTROL_CENTER_SPLIT_RATIO).toBe(70)
    expect(globalThis.localStorage.setItem).toHaveBeenCalledWith('matoLoopWorkspaceSplitRatio', '67')
  })

  it('falls back when the persisted split ratio is invalid', () => {
    globalThis.localStorage = storage('invalid')
    expect(useControlCenterStore().splitRatio).toBe(DEFAULT_CONTROL_CENTER_SPLIT_RATIO)
  })

  it.each(['notifications', 'approvals'])('opens %s as notifications while persisting the panel state', async (appId) => {
    api.patch.mockResolvedValue({
      data: {
        controlCenter: {
          tabs: [tab('files')],
          activeAppId: 'files',
          open: true,
          fileView: null
        }
      }
    })
    const store = useControlCenterStore()
    store.syncSession({ id: 'session-1', controlCenter: { tabs: [tab('files')], activeAppId: 'files' } })

    await store.openApp(appId)
    expect(store.open).toBe(true)
    expect(store.activeGlobalSectionId).toBe('notifications')
    expect(store.activeContentId).toBe('notifications')
    expect(store.tabs).toEqual([tab('session'), tab('files')])
    expect(api.patch).toHaveBeenCalledOnce()

    store.syncSession({ id: 'session-2', controlCenter: { tabs: [tab('calendar')], activeAppId: 'calendar' } })
    expect(store.activeContentId).toBe('calendar')
    await store.activate('calendar')
    expect(store.activeGlobalSectionId).toBeNull()
    expect(store.activeContentId).toBe('calendar')
  })

  it('normalizes legacy global and pinned tabs to ordered integrations', () => {
    const store = useControlCenterStore()
    store.syncSession({
      id: 'session-1',
      controlCenter: {
        tabs: [
          tab('files'),
          tab('notifications'),
          { appId: 'test-app', pinned: true },
          tab('approvals'),
          tab('calendar')
        ],
        activeAppId: 'notifications'
      }
    })

    expect(store.tabs).toEqual([tab('session'), tab('test-app'), tab('files'), tab('calendar')])
    expect(store.activeAppId).toBe('session')
  })

  it('adds and persists an integration for the active session', async () => {
    api.patch.mockResolvedValue(response([tab('files'), tab('calendar')], 'calendar'))
    const store = useControlCenterStore()
    store.syncSession({ id: 'session-1', controlCenter: { tabs: [tab('files')], activeAppId: 'files' } })

    await store.openApp('calendar')

    expect(store.activeContentId).toBe('calendar')
    expect(api.patch).toHaveBeenCalledWith('/sessions/session-1', {
      control_center: requestState(['session', 'files', 'calendar'], 'calendar', true)
    })
  })

  it('adds a background integration without opening or activating the panel', async () => {
    api.patch.mockResolvedValue(response([tab('files'), tab('browser')], 'files', false))
    const store = useControlCenterStore()
    store.syncSession({ id: 'session-1', controlCenter: { tabs: [tab('files')], activeAppId: 'files' } })

    await store.ensureApp('browser')
    await store.ensureApp('browser')

    expect(store.open).toBe(false)
    expect(store.activeAppId).toBe('files')
    expect(api.patch).toHaveBeenCalledOnce()
    expect(api.patch).toHaveBeenCalledWith('/sessions/session-1', {
      control_center: requestState(['session', 'files', 'browser'], 'files', false)
    })
  })

  it('reorders and removes integrations explicitly', async () => {
    api.patch
      .mockResolvedValueOnce(response([tab('calendar'), tab('files'), tab('test-app')], 'files'))
      .mockResolvedValueOnce(response([tab('calendar'), tab('test-app')], 'calendar'))
    const store = useControlCenterStore()
    store.syncSession({
      id: 'session-1',
      controlCenter: { tabs: [tab('files'), tab('calendar'), tab('test-app')], activeAppId: 'files' }
    })

    await store.moveIntegration('files', 1)
    expect(store.tabs.map((item) => item.appId)).toEqual(['session', 'calendar', 'files', 'test-app'])

    await store.removeIntegration('files')
    expect(store.tabs.map((item) => item.appId)).toEqual(['session', 'calendar', 'test-app'])
    expect(store.activeAppId).toBe('calendar')
  })

  it('restores the prior server state after a failed mutation', async () => {
    api.patch.mockRejectedValue(new Error('request failed'))
    const store = useControlCenterStore()
    store.syncSession({ id: 'session-1', controlCenter: { tabs: [tab('files')], activeAppId: 'files' } })

    await expect(store.openApp('calendar')).rejects.toThrow('request failed')

    expect(store.tabs).toEqual([tab('session'), tab('files')])
    expect(store.activeAppId).toBe('files')
  })

  it('unlocks persistence when the session changes during a save', async () => {
    let resolveFirst
    api.patch
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce(response([tab('files'), tab('test-app')], 'test-app'))
    const store = useControlCenterStore()
    store.syncSession({ id: 'session-1', controlCenter: { tabs: [tab('files')], activeAppId: 'files' } })
    const oldSave = store.openApp('calendar')

    store.syncSession({ id: 'session-2', controlCenter: { tabs: [tab('files')], activeAppId: 'files' } })
    expect(store.saving).toBe(false)
    await store.openApp('test-app')
    resolveFirst(response([tab('files'), tab('calendar')], 'calendar'))
    await oldSave

    expect(store.sessionId).toBe('session-2')
    expect(store.activeAppId).toBe('test-app')
    expect(store.saving).toBe(false)
  })

  it('persists the latest overlapping integration state', async () => {
    let resolveFirst
    api.patch
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce(response([tab('files'), tab('calendar'), tab('test-app')], 'test-app'))
    const store = useControlCenterStore()
    store.syncSession({ id: 'session-1', controlCenter: { tabs: [tab('files')], activeAppId: 'files' } })

    const first = store.openApp('calendar')
    await store.openApp('test-app')
    resolveFirst(response([tab('files'), tab('calendar')], 'calendar'))
    await first

    expect(api.patch).toHaveBeenCalledTimes(2)
    expect(store.activeAppId).toBe('test-app')
    expect(store.tabs.map((item) => item.appId)).toEqual(['session', 'files', 'calendar', 'test-app'])
  })

  it('persists draft integration changes made after session creation starts', async () => {
    const store = useControlCenterStore()
    await store.openApp('calendar')
    api.patch.mockResolvedValue(response([tab('files'), tab('calendar')], 'calendar'))

    const state = await store.attachCreatedSession('session-1', {
      tabs: [tab('files')],
      activeAppId: 'files'
    })

    expect(api.patch).toHaveBeenCalledWith('/sessions/session-1', {
      control_center: requestState(['session', 'files', 'calendar'], 'calendar', true)
    })
    expect(state.activeAppId).toBe('calendar')
  })

  it('restores and persists the open file view as session state', async () => {
    const store = useControlCenterStore()
    store.syncSession({
      id: 'session-1',
      controlCenter: {
        tabs: [tab('files')],
        activeAppId: 'files',
        open: true,
        fileView: sessionFileView
      }
    })
    api.patch.mockResolvedValue({
      data: {
        controlCenter: {
          tabs: [tab('files')],
          activeAppId: 'files',
          open: false,
          fileView: sessionFileView
        }
      }
    })

    await store.setOpen(false)

    expect(store.open).toBe(false)
    expect(store.fileView).toEqual(sessionFileView)
    expect(api.patch).toHaveBeenCalledWith('/sessions/session-1', {
      control_center: requestState(
        ['session', 'files'],
        'files',
        false,
        {
          scope: 'session',
          selected_path: sessionFileView.selectedPath,
          selected_kind: 'file',
          viewed_file_path: sessionFileView.viewedFilePath,
          root_identity: null,
          execution_agent_id: null,
          current_directory: null
        },
        {
          session: {
            scope: 'session',
            selected_path: sessionFileView.selectedPath,
            selected_kind: 'file',
            viewed_file_path: sessionFileView.viewedFilePath,
            root_identity: null,
            execution_agent_id: null,
            current_directory: null
          },
          filesystem: null
        }
      )
    })
  })

  it('opens each file once as a persistent resource tab', async () => {
    const store = useControlCenterStore()
    const target = {
      scope: 'session',
      path: 'notes/plan.md',
      name: 'plan.md',
      rootIdentity: '550e8400-e29b-41d4-a716-446655440000',
      readOnly: false
    }

    const tabId = await store.openFile(target)
    expect(tabId).toMatch(/^file-/u)
    expect(store.activeAppId).toBe(tabId)
    expect(store.fileTabs).toEqual([expect.objectContaining({
      tabId,
      kind: 'file',
      appId: 'file-viewer',
      fileTarget: target
    })])

    await expect(store.openFile(target)).resolves.toBe(tabId)
    expect(store.fileTabs).toHaveLength(1)

    await store.setOpen(false)
    await store.openApp(tabId)
    expect(store.open).toBe(true)
    expect(store.activeAppId).toBe(tabId)

    await store.rebindFileTargets(target, 'notes/final.md')
    expect(store.fileTabs[0].fileTarget).toEqual({
      ...target,
      path: 'notes/final.md',
      name: 'final.md'
    })
    await store.rebindFileTargets(store.fileTabs[0].fileTarget)
    expect(store.fileTabs).toEqual([])
    expect(store.activeAppId).toBe('files')
  })

  it('keys historical tabs by root, file identity and revision independently of the current file', async () => {
    const store = useControlCenterStore()
    const target = {
      scope: 'session', path: 'notes.md', name: 'notes.md',
      rootIdentity: '550e8400-e29b-41d4-a716-446655440000',
      fileId: '550e8400-e29b-41d4-a716-446655440001', revision: 1
    }
    const historical = await store.openFile(target)
    expect(store.activeTab.fileTarget.readOnly).toBe(true)
    expect(await store.openFile({ ...target, path: 'renamed.md' })).toBe(historical)
    expect(await store.openFile({ ...target, revision: 2 })).not.toBe(historical)
    expect(await store.openFile({ ...target, revision: null })).not.toBe(historical)
    expect(await store.openFile({ ...target, rootIdentity: '550e8400-e29b-41d4-a716-446655440002' })).not.toBe(historical)
    expect(store.fileTabs).toHaveLength(4)
    await store.rebindFileTargets(target, 'renamed.md')
    expect(store.fileTabs.find((tab) => tab.tabId === historical).fileTarget).toMatchObject({
      path: 'renamed.md', fileId: target.fileId, revision: 1, readOnly: true
    })
    await store.rebindFileTargets({ ...target, path: 'renamed.md' })
    expect(store.fileTabs).toHaveLength(1)
    expect(store.fileTabs[0].fileTarget.rootIdentity).not.toBe(target.rootIdentity)
  })

  it('defers integration shortcuts until authoritative session state is loaded', async () => {
    const store = useControlCenterStore()
    store.bindSession({
      id: 'session-1',
      controlCenter: { tabs: [tab('files')], activeAppId: 'files' }
    })

    await store.openApp('calendar')
    expect(api.patch).not.toHaveBeenCalled()
    expect(store.requestedAppId).toBe('calendar')
    api.patch.mockResolvedValue(response([tab('files'), tab('calendar')], 'calendar'))
    store.syncSession({
      id: 'session-1',
      controlCenter: { tabs: [tab('files')], activeAppId: 'files' }
    })
    await vi.waitFor(() => expect(api.patch).toHaveBeenCalled())

    expect(api.patch.mock.calls[0][1].control_center.tabs).toEqual([
      requestTab('session'),
      requestTab('files'),
      requestTab('calendar')
    ])
  })

  it.each(['calendar', 'notifications', 'approvals'])('does not carry a deferred %s shortcut into another session', async (appId) => {
    const store = useControlCenterStore()
    store.bindSession({
      id: 'session-1',
      controlCenter: { tabs: [tab('files')], activeAppId: 'files' }
    })
    await store.openApp(appId)

    store.bindSession({
      id: 'session-2',
      controlCenter: { tabs: [tab('files')], activeAppId: 'files' }
    })
    store.syncSession({
      id: 'session-2',
      controlCenter: { tabs: [tab('files')], activeAppId: 'files' }
    })

    expect(store.requestedAppId).toBeNull()
    expect(store.activeGlobalSectionId).toBeNull()
    expect(store.activeAppId).toBe('files')
    expect(api.patch).not.toHaveBeenCalled()
  })

  it.each(['notifications', 'approvals'])('opens %s immediately and defers persistence until session state is loaded', async (appId) => {
    const store = useControlCenterStore()
    store.bindSession({
      id: 'session-1',
      controlCenter: { tabs: [tab('files')], activeAppId: 'files' }
    })

    await store.openApp(appId)

    expect(store.activeGlobalSectionId).toBe('notifications')
    expect(store.open).toBe(true)
    expect(store.requestedAppId).toBe('notifications')
    expect(api.patch).not.toHaveBeenCalled()

    api.patch.mockResolvedValue({
      data: { controlCenter: { tabs: [tab('files')], activeAppId: 'files', open: true } }
    })
    store.syncSession({
      id: 'session-1',
      controlCenter: { tabs: [tab('files')], activeAppId: 'files', open: false }
    })
    await vi.waitFor(() => expect(api.patch).toHaveBeenCalledOnce())

    expect(store.activeContentId).toBe('notifications')
    expect(store.open).toBe(true)
    expect(store.requestedAppId).toBeNull()
    expect(api.patch).toHaveBeenCalledWith('/sessions/session-1', {
      control_center: requestState(['session', 'files'], 'files', true)
    })
  })
})
