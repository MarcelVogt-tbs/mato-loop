import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  PWA_UPDATE_RELOAD_KEY,
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
  it('marks a requested update before reloading', () => {
    const setItem = vi.fn()
    const reload = vi.fn()
    Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: { setItem } })
    Object.defineProperty(globalThis, 'window', { configurable: true, value: { location: { reload } } })

    requestPwaUpdate()

    expect(setItem).toHaveBeenCalledWith(PWA_UPDATE_RELOAD_KEY, '1')
    expect(reload).toHaveBeenCalledOnce()
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
