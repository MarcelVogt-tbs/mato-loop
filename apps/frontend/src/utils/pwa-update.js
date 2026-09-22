export const PWA_UPDATE_EVENT = 'matoloop:pwa-update'
export const PWA_BEFORE_UPDATE_EVENT = 'matoloop:pwa-before-update'
export const PWA_UPDATE_SNOOZE_KEY = 'matoloop:pwa-update-snooze-until'

const PWA_UPDATE_SNOOZE_MS = 60 * 60 * 1000

export function announcePwaUpdate() {
  window.dispatchEvent(new Event(PWA_UPDATE_EVENT))
}

export async function requestPwaUpdate() {
  const serviceWorker = navigator.serviceWorker
  const registration = await serviceWorker?.getRegistration?.()
  if (!window.dispatchEvent(new Event(PWA_BEFORE_UPDATE_EVENT, { cancelable: true }))) return false

  if (!registration?.waiting) {
    window.location.reload()
    return true
  }

  const reload = () => window.location.reload()
  serviceWorker.addEventListener('controllerchange', reload, { once: true })
  try {
    registration.waiting.postMessage({ type: 'SKIP_WAITING' })
  } catch (error) {
    serviceWorker.removeEventListener('controllerchange', reload)
    throw error
  }
  return true
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
