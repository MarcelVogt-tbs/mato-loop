import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('workbox-precaching', () => ({
  precache: vi.fn(),
  cleanupOutdatedCaches: vi.fn(),
  getCacheKeyForURL: vi.fn(),
  matchPrecache: vi.fn()
}))

const originalNavigator = globalThis.navigator
const originalSessionStorage = globalThis.sessionStorage
const originalWindow = globalThis.window
let updates

beforeEach(async () => {
  vi.resetModules()
  updates = await import('../src/utils/pwa-update')
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: originalNavigator })
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: originalSessionStorage })
  Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
})

describe('PWA updates', () => {
  function updateEnvironment({ controlled = true, waiting = true } = {}) {
    const reload = vi.fn()
    const worker = Object.assign(new EventTarget(), { postMessage: vi.fn(), state: 'installed' })
    const serviceWorker = Object.assign(new EventTarget(), {
      controller: controlled ? { state: 'activated' } : null,
      getRegistration: vi.fn().mockResolvedValue({ waiting: waiting ? worker : null })
    })
    const window = Object.assign(new EventTarget(), {
      location: { reload }, setTimeout, clearTimeout
    })
    Object.defineProperty(globalThis, 'window', { configurable: true, value: window })
    Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { serviceWorker } })
    updates.observePwaUpdates()
    return { reload, serviceWorker, worker }
  }

  it('activates the waiting worker and reloads once after takeover', async () => {
    const { reload, serviceWorker, worker } = updateEnvironment()
    const update = updates.requestPwaUpdate()
    await Promise.resolve()

    expect(worker.postMessage).toHaveBeenCalledWith({ type: 'SKIP_WAITING' })
    expect(reload).not.toHaveBeenCalled()
    serviceWorker.controller = worker
    serviceWorker.dispatchEvent(new Event('controllerchange'))
    serviceWorker.dispatchEvent(new Event('controllerchange'))

    await expect(update).resolves.toBe(true)
    expect(reload).toHaveBeenCalledOnce()
    expect(updates.isPwaUpdateReload()).toBe(true)
  })

  it('reloads another open tab on takeover', () => {
    const { reload, serviceWorker } = updateEnvironment()
    serviceWorker.controller = { state: 'activated' }
    serviceWorker.dispatchEvent(new Event('controllerchange'))
    expect(reload).toHaveBeenCalledOnce()
  })

  it('does not reload on the first installation', () => {
    const { reload, serviceWorker, worker } = updateEnvironment({ controlled: false })
    serviceWorker.controller = worker
    serviceWorker.dispatchEvent(new Event('controllerchange'))
    expect(reload).not.toHaveBeenCalled()
  })

  it('answers the old page window count request on the waiting worker', async () => {
    const clients = { matchAll: vi.fn().mockResolvedValue([{}]) }
    const worker = Object.assign(new EventTarget(), {
      __WB_MANIFEST: [], clients, skipWaiting: vi.fn().mockResolvedValue()
    })
    vi.stubGlobal('self', worker)
    await import('../src-pwa/custom-service-worker')

    const channel = new MessageChannel()
    const answer = new Promise((resolve) => { channel.port1.onmessage = (event) => resolve(event.data) })
    let pending
    worker.dispatchEvent(Object.assign(new Event('message'), {
      data: { type: 'WINDOW_COUNT' },
      ports: [channel.port2],
      waitUntil: (promise) => { pending = promise }
    }))

    expect(pending).toBeDefined()
    await pending
    await expect(answer).resolves.toBe(1)
    expect(clients.matchAll).toHaveBeenCalledWith({ type: 'window', includeUncontrolled: true })
    worker.dispatchEvent(Object.assign(new Event('message'), {
      data: { type: 'SKIP_WAITING' }, waitUntil: (promise) => { pending = promise }
    }))
    await pending
    expect(worker.skipWaiting).toHaveBeenCalledOnce()
    channel.port1.close()
    channel.port2.close()
  })

  it('does not reload if no update is waiting', async () => {
    const { reload } = updateEnvironment({ waiting: false })
    await expect(updates.requestPwaUpdate()).resolves.toBe(false)
    expect(reload).not.toHaveBeenCalled()
  })

  it('recovers from a stalled activation and reloads on a late takeover', async () => {
    vi.useFakeTimers()
    const { reload, serviceWorker, worker } = updateEnvironment()
    const update = updates.requestPwaUpdate()
    const failure = expect(update).rejects.toThrow('timed out')
    await vi.advanceTimersByTimeAsync(15_000)

    await failure
    expect(reload).not.toHaveBeenCalled()
    serviceWorker.controller = worker
    serviceWorker.dispatchEvent(new Event('controllerchange'))
    expect(reload).toHaveBeenCalledOnce()
  })

  it('allows retry after a failed message', async () => {
    const { reload, serviceWorker, worker } = updateEnvironment()
    worker.postMessage.mockImplementationOnce(() => { throw new Error('Worker unavailable') })
    await expect(updates.requestPwaUpdate()).rejects.toThrow('Worker unavailable')

    const retry = updates.requestPwaUpdate()
    await Promise.resolve()
    expect(worker.postMessage).toHaveBeenCalledTimes(2)
    serviceWorker.controller = worker
    serviceWorker.dispatchEvent(new Event('controllerchange'))
    await expect(retry).resolves.toBe(true)
    expect(reload).toHaveBeenCalledOnce()
  })

  it('snoozes an offered update for one hour even when storage is unavailable', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-21T12:00:00Z'))
    const storage = {
      getItem: vi.fn(() => null),
      removeItem: vi.fn(),
      setItem: vi.fn(() => { throw new Error('Storage unavailable') })
    }
    Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: storage })

    updates.snoozePwaUpdate()
    expect(storage.setItem).toHaveBeenCalledWith(
      updates.PWA_UPDATE_SNOOZE_KEY,
      String(Date.now() + 60 * 60 * 1000)
    )
    expect(updates.pwaUpdateSnoozeRemaining()).toBe(60 * 60 * 1000)
    vi.advanceTimersByTime(60 * 60 * 1000)
    expect(updates.pwaUpdateSnoozeRemaining()).toBe(0)
  })
})
