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
const testIntegrationIds = ['calendar', 'git']

function storage(initialValue = null) {
  let value = initialValue
  return {
    getItem: vi.fn(() => value),
    setItem: vi.fn((_key, nextValue) => { value = nextValue })
  }
}

function tab(appId) {
  return { appId, pinned: false }
}

function response(tabs, activeAppId) {
  return { data: { controlCenter: { tabs, activeAppId } } }
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
    vi.clearAllMocks()
  })

  afterEach(() => {
    testIntegrationIds.forEach((id) => controlCenterIntegrationIds.delete(id))
    if (originalLocalStorage) globalThis.localStorage = originalLocalStorage
    else delete globalThis.localStorage
  })

  it('starts drafts with the files integration', () => {
    const store = useControlCenterStore()
    expect(store.tabs).toEqual([tab('files')])
    expect(store.activeAppId).toBe('files')
    expect(store.activeContentId).toBe('files')
    expect(store.open).toBe(false)
    expect(store.fileView).toBeNull()
  })

  it('keeps global sections outside the integration manifest', () => {
    expect([...controlCenterGlobalSectionIds]).toEqual(['notifications', 'approvals'])
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

  it('opens global sections while persisting the panel state', async () => {
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

    await store.openApp('notifications')
    expect(store.open).toBe(true)
    expect(store.activeGlobalSectionId).toBe('notifications')
    expect(store.activeContentId).toBe('notifications')
    expect(store.tabs).toEqual([tab('files')])
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
          { appId: 'git', pinned: true },
          tab('approvals'),
          tab('calendar')
        ],
        activeAppId: 'notifications'
      }
    })

    expect(store.tabs).toEqual([tab('git'), tab('files'), tab('calendar')])
    expect(store.activeAppId).toBe('git')
  })

  it('adds and persists an integration for the active session', async () => {
    api.patch.mockResolvedValue(response([tab('files'), tab('calendar')], 'calendar'))
    const store = useControlCenterStore()
    store.syncSession({ id: 'session-1', controlCenter: { tabs: [tab('files')], activeAppId: 'files' } })

    await store.openApp('calendar')

    expect(store.activeContentId).toBe('calendar')
    expect(api.patch).toHaveBeenCalledWith('/sessions/session-1', {
      control_center: {
        tabs: [
          { app_id: 'files', pinned: false },
          { app_id: 'calendar', pinned: false }
        ],
        active_app_id: 'calendar',
        open: true,
        file_view: null
      }
    })
  })

  it('reorders and removes integrations explicitly', async () => {
    api.patch
      .mockResolvedValueOnce(response([tab('calendar'), tab('files'), tab('git')], 'files'))
      .mockResolvedValueOnce(response([tab('calendar'), tab('git')], 'calendar'))
    const store = useControlCenterStore()
    store.syncSession({
      id: 'session-1',
      controlCenter: { tabs: [tab('files'), tab('calendar'), tab('git')], activeAppId: 'files' }
    })

    await store.moveIntegration('files', 1)
    expect(store.tabs.map((item) => item.appId)).toEqual(['calendar', 'files', 'git'])

    await store.removeIntegration('files')
    expect(store.tabs.map((item) => item.appId)).toEqual(['calendar', 'git'])
    expect(store.activeAppId).toBe('calendar')
  })

  it('restores the prior server state after a failed mutation', async () => {
    api.patch.mockRejectedValue(new Error('request failed'))
    const store = useControlCenterStore()
    store.syncSession({ id: 'session-1', controlCenter: { tabs: [tab('files')], activeAppId: 'files' } })

    await expect(store.openApp('calendar')).rejects.toThrow('request failed')

    expect(store.tabs).toEqual([tab('files')])
    expect(store.activeAppId).toBe('files')
  })

  it('unlocks persistence when the session changes during a save', async () => {
    let resolveFirst
    api.patch
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce(response([tab('files'), tab('git')], 'git'))
    const store = useControlCenterStore()
    store.syncSession({ id: 'session-1', controlCenter: { tabs: [tab('files')], activeAppId: 'files' } })
    const oldSave = store.openApp('calendar')

    store.syncSession({ id: 'session-2', controlCenter: { tabs: [tab('files')], activeAppId: 'files' } })
    expect(store.saving).toBe(false)
    await store.openApp('git')
    resolveFirst(response([tab('files'), tab('calendar')], 'calendar'))
    await oldSave

    expect(store.sessionId).toBe('session-2')
    expect(store.activeAppId).toBe('git')
    expect(store.saving).toBe(false)
  })

  it('persists the latest overlapping integration state', async () => {
    let resolveFirst
    api.patch
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce(response([tab('files'), tab('calendar'), tab('git')], 'git'))
    const store = useControlCenterStore()
    store.syncSession({ id: 'session-1', controlCenter: { tabs: [tab('files')], activeAppId: 'files' } })

    const first = store.openApp('calendar')
    await store.openApp('git')
    resolveFirst(response([tab('files'), tab('calendar')], 'calendar'))
    await first

    expect(api.patch).toHaveBeenCalledTimes(2)
    expect(store.activeAppId).toBe('git')
    expect(store.tabs.map((item) => item.appId)).toEqual(['files', 'calendar', 'git'])
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
      control_center: {
        tabs: [
          { app_id: 'files', pinned: false },
          { app_id: 'calendar', pinned: false }
        ],
        active_app_id: 'calendar',
        open: true,
        file_view: null
      }
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
      control_center: {
        tabs: [{ app_id: 'files', pinned: false }],
        active_app_id: 'files',
        open: false,
        file_view: {
          scope: 'session',
          selected_path: sessionFileView.selectedPath,
          selected_kind: 'file',
          viewed_file_path: sessionFileView.viewedFilePath,
          root_identity: null,
          execution_agent_id: null,
          current_directory: null
        }
      }
    })
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
      { app_id: 'files', pinned: false },
      { app_id: 'calendar', pinned: false }
    ])
  })

  it('does not carry a deferred integration shortcut into another session', async () => {
    const store = useControlCenterStore()
    store.bindSession({
      id: 'session-1',
      controlCenter: { tabs: [tab('files')], activeAppId: 'files' }
    })
    await store.openApp('calendar')

    store.bindSession({
      id: 'session-2',
      controlCenter: { tabs: [tab('files')], activeAppId: 'files' }
    })
    store.syncSession({
      id: 'session-2',
      controlCenter: { tabs: [tab('files')], activeAppId: 'files' }
    })

    expect(store.requestedAppId).toBeNull()
    expect(store.activeAppId).toBe('files')
    expect(api.patch).not.toHaveBeenCalled()
  })

  it('opens global shortcuts immediately while session state is loading', async () => {
    const store = useControlCenterStore()
    store.bindSession({
      id: 'session-1',
      controlCenter: { tabs: [tab('files')], activeAppId: 'files' }
    })

    await store.openApp('approvals')

    expect(store.activeGlobalSectionId).toBe('approvals')
    expect(store.requestedAppId).toBeNull()
    expect(api.patch).not.toHaveBeenCalled()
  })
})
