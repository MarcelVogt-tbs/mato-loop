export const PWA_UPDATE_EVENT = 'matoloop:pwa-update'
export const PWA_UPDATE_SNOOZE_KEY = 'matoloop:pwa-update-snooze-until'

const PWA_UPDATE_SNOOZE_MS = 60 * 60 * 1000
const PWA_ACTIVATION_TIMEOUT_MS = 15_000
let snoozedUntil = 0
let updating = false
let reloading = false

export function announcePwaUpdate() {
  window.dispatchEvent(new Event(PWA_UPDATE_EVENT))
}

export function isPwaUpdateReload() {
  return reloading
}

export function observePwaUpdates() {
  const serviceWorker = navigator.serviceWorker
  if (!serviceWorker) return
  let controller = serviceWorker.controller
  serviceWorker.addEventListener('controllerchange', () => {
    const next = serviceWorker.controller
    if (next && next !== controller && (controller || updating) && !reloading) {
      reloading = true
      window.location.reload()
    }
    controller = next
  })
}

export async function requestPwaUpdate() {
  const serviceWorker = navigator.serviceWorker
  if (updating) return false
  const registration = await serviceWorker?.getRegistration?.()
  const waiting = registration?.waiting
  if (!waiting) return false

  updating = true
  try {
    await new Promise((resolve, reject) => {
      const finish = (error) => {
        window.clearTimeout(timer)
        serviceWorker.removeEventListener('controllerchange', onChange)
        waiting.removeEventListener('statechange', onStateChange)
        if (error) reject(error)
        else resolve()
      }
      const onChange = () => {
        if (serviceWorker.controller === waiting) finish()
      }
      const onStateChange = () => {
        if (waiting.state === 'redundant') finish(new Error('Service worker was replaced.'))
      }
      const timer = window.setTimeout(() => finish(new Error('Service worker activation timed out.')), PWA_ACTIVATION_TIMEOUT_MS)
      serviceWorker.addEventListener('controllerchange', onChange)
      waiting.addEventListener('statechange', onStateChange)
      try {
        waiting.postMessage({ type: 'SKIP_WAITING' })
      } catch (error) {
        finish(error)
      }
    })
    return true
  } finally {
    updating = false
  }
}

export function snoozePwaUpdate() {
  snoozedUntil = Date.now() + PWA_UPDATE_SNOOZE_MS
  try {
    sessionStorage.setItem(PWA_UPDATE_SNOOZE_KEY, String(snoozedUntil))
  } catch {
    // The current tab still keeps its snooze when storage is unavailable.
  }
}

export function pwaUpdateSnoozeRemaining() {
  try {
    snoozedUntil = Math.max(snoozedUntil, Number(sessionStorage.getItem(PWA_UPDATE_SNOOZE_KEY)) || 0)
  } catch {
    // Use the in-memory deadline.
  }
  const remaining = snoozedUntil - Date.now()
  if (remaining > 0) return remaining
  snoozedUntil = 0
  try {
    sessionStorage.removeItem(PWA_UPDATE_SNOOZE_KEY)
  } catch {
    // Storage is optional for update prompts.
  }
  return 0
}
