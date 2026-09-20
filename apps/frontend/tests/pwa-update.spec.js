import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  PWA_UPDATE_RELOAD_KEY,
  pwaWindowCount,
  requestPwaUpdate
} from '../src/utils/pwa-update'

const originalNavigator = globalThis.navigator
const originalSessionStorage = globalThis.sessionStorage
const originalWindow = globalThis.window

afterEach(() => {
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
})
