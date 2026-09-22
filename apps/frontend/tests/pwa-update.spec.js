import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  PWA_BEFORE_UPDATE_EVENT,
  PWA_UPDATE_SNOOZE_KEY,
  pwaUpdateSnoozeRemaining,
  pwaWindowCount,
  requestPwaUpdate,
  snoozePwaUpdate
} from '../src/utils/pwa-update'

const originalNavigator = globalThis.navigator
const originalSessionStorage = globalThis.sessionStorage
const originalWindow = globalThis.window

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: originalNavigator })
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: originalSessionStorage })
  Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
})

describe('PWA updates', () => {
  function updateEnvironment(waiting = { postMessage: vi.fn() }) {
    const reload = vi.fn()
    const serviceWorker = Object.assign(new EventTarget(), {
      getRegistration: vi.fn().mockResolvedValue({ waiting })
    })
    const window = Object.assign(new EventTarget(), { location: { reload } })
    Object.defineProperty(globalThis, 'window', { configurable: true, value: window })
    Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { serviceWorker } })
    return { reload, serviceWorker, waiting, window }
  }

  it('activates the waiting worker before reloading exactly once on takeover', async () => {
    const { reload, serviceWorker, waiting } = updateEnvironment()

    await expect(requestPwaUpdate()).resolves.toBe(true)

    expect(waiting.postMessage).toHaveBeenCalledWith({ type: 'SKIP_WAITING' })
    expect(reload).not.toHaveBeenCalled()
    serviceWorker.dispatchEvent(new Event('controllerchange'))
    serviceWorker.dispatchEvent(new Event('controllerchange'))
    expect(reload).toHaveBeenCalledOnce()
  })

  it('allows unsaved changes or recording guards to block activation before takeover', async () => {
    const { reload, serviceWorker, waiting, window } = updateEnvironment()
    window.addEventListener(PWA_BEFORE_UPDATE_EVENT, (event) => event.preventDefault(), { once: true })

    await expect(requestPwaUpdate()).resolves.toBe(false)

    expect(waiting.postMessage).not.toHaveBeenCalled()
    serviceWorker.dispatchEvent(new Event('controllerchange'))
    expect(reload).not.toHaveBeenCalled()

    await expect(requestPwaUpdate()).resolves.toBe(true)
    expect(waiting.postMessage).toHaveBeenCalledOnce()
    serviceWorker.dispatchEvent(new Event('controllerchange'))
    expect(reload).toHaveBeenCalledOnce()
  })

  it('reloads if the offered worker has already activated', async () => {
    const { reload } = updateEnvironment(null)

    await expect(requestPwaUpdate()).resolves.toBe(true)
    expect(reload).toHaveBeenCalledOnce()
  })

  it('removes the reload listener if sending the activation request fails', async () => {
    const { reload, serviceWorker } = updateEnvironment({
      postMessage: vi.fn(() => { throw new Error('Worker unavailable') })
    })

    await expect(requestPwaUpdate()).rejects.toThrow('Worker unavailable')
    serviceWorker.dispatchEvent(new Event('controllerchange'))
    expect(reload).not.toHaveBeenCalled()
  })

  it('counts controlled app windows before activating an update', async () => {
    const postMessage = vi.fn((message, ports) => {
      expect(message).toEqual({ type: 'WINDOW_COUNT' })
      ports[0].postMessage(2)
    })
    Object.defineProperty(globalThis, 'navigator', {
      configurable: true,
      value: { serviceWorker: { controller: { postMessage } } }
    })
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: { setTimeout, clearTimeout }
    })

    await expect(pwaWindowCount()).resolves.toBe(2)
    expect(postMessage).toHaveBeenCalledOnce()
  })

  it('asks the waiting worker from the offered update for the window count', async () => {
    const waitingPostMessage = vi.fn((message, ports) => {
      expect(message).toEqual({ type: 'WINDOW_COUNT' })
      ports[0].postMessage(1)
    })
    const controllerPostMessage = vi.fn()
    Object.defineProperty(globalThis, 'navigator', {
      configurable: true,
      value: {
        serviceWorker: {
          controller: { postMessage: controllerPostMessage },
          getRegistration: vi.fn().mockResolvedValue({ waiting: { postMessage: waitingPostMessage } })
        }
      }
    })
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: { setTimeout, clearTimeout }
    })

    await expect(pwaWindowCount()).resolves.toBe(1)
    expect(waitingPostMessage).toHaveBeenCalledOnce()
    expect(controllerPostMessage).not.toHaveBeenCalled()
  })

  it('snoozes an offered update for one hour', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-21T12:00:00Z'))
    const values = new Map()
    const storage = {
      getItem: vi.fn((key) => values.get(key) ?? null),
      removeItem: vi.fn((key) => values.delete(key)),
      setItem: vi.fn((key, value) => values.set(key, value))
    }
    Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: storage })

    snoozePwaUpdate()

    expect(storage.setItem).toHaveBeenCalledWith(
      PWA_UPDATE_SNOOZE_KEY,
      String(Date.now() + 60 * 60 * 1000)
    )
    expect(pwaUpdateSnoozeRemaining()).toBe(60 * 60 * 1000)

    vi.advanceTimersByTime(60 * 60 * 1000)
    expect(pwaUpdateSnoozeRemaining()).toBe(0)
    expect(storage.removeItem).toHaveBeenCalledWith(PWA_UPDATE_SNOOZE_KEY)
  })
})
