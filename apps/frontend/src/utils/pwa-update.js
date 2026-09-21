export const PWA_UPDATE_EVENT = 'matoloop:pwa-update'
export const PWA_UPDATE_RELOAD_KEY = 'matoloop:pwa-update-reload'
export const PWA_UPDATE_SNOOZE_KEY = 'matoloop:pwa-update-snooze-until'

const PWA_UPDATE_SNOOZE_MS = 60 * 60 * 1000

export function announcePwaUpdate() {
  window.dispatchEvent(new Event(PWA_UPDATE_EVENT))
}

export function requestPwaUpdate() {
  sessionStorage.setItem(PWA_UPDATE_RELOAD_KEY, '1')
  window.location.reload()
}

export function snoozePwaUpdate() {
  sessionStorage.setItem(PWA_UPDATE_SNOOZE_KEY, String(Date.now() + PWA_UPDATE_SNOOZE_MS))
}

export function pwaUpdateSnoozeRemaining() {
  const remaining = Number(sessionStorage.getItem(PWA_UPDATE_SNOOZE_KEY)) - Date.now()
  if (remaining > 0) return remaining
  sessionStorage.removeItem(PWA_UPDATE_SNOOZE_KEY)
  return 0
}

export async function pwaWindowCount() {
  const serviceWorker = navigator.serviceWorker
  let registration = null
  try {
    registration = await serviceWorker?.getRegistration?.()
  } catch {
    // Fall back to the active worker when registration lookup fails.
  }
  const worker = registration?.waiting || serviceWorker?.controller
  if (!worker) return 1

  return new Promise((resolve) => {
    const channel = new MessageChannel()
    const finish = (value) => {
      window.clearTimeout(timer)
      channel.port1.close()
      resolve(value)
    }
    const timer = window.setTimeout(() => finish(null), 1000)
    channel.port1.onmessage = (event) => {
      finish(event.data)
    }
    try {
      worker.postMessage({ type: 'WINDOW_COUNT' }, [channel.port2])
    } catch {
      finish(null)
    }
  })
}
