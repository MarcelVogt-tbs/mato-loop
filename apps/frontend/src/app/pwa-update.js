export const PWA_UPDATE_EVENT = 'matoloop:pwa-update'
export const PWA_UPDATE_SNOOZE_KEY = 'matoloop:pwa-update-snooze-until'
const PWA_CHANGELOG_KEY = 'matoloop:pwa-changelog'
const WEB_REVISION = process.env.MATOLOOP_WEB_REVISION || ''
const REVISION_PATTERN = /^[0-9a-f]{40}$/

const PWA_UPDATE_SNOOZE_MS = 60 * 60 * 1000
const PWA_ACTIVATION_TIMEOUT_MS = 15_000
let snoozedUntil = 0
let updating = false
let reloading = false
let changelogIntent = null

export function consumePwaChangelog(userId) {
  try {
    const raw = sessionStorage.getItem(PWA_CHANGELOG_KEY)
    sessionStorage.removeItem(PWA_CHANGELOG_KEY)
    if (!raw) return null
    const intent = JSON.parse(raw)
    if (intent.userId !== userId || !REVISION_PATTERN.test(intent.base)
      || !REVISION_PATTERN.test(WEB_REVISION) || intent.base === WEB_REVISION
      || !Number.isFinite(intent.createdAt) || Date.now() - intent.createdAt > 10 * 60 * 1000
      || intent.createdAt > Date.now()) return null
    return { base: intent.base, target: WEB_REVISION }
  } catch {
    // Changelog display is optional when tab storage is unavailable.
    return null
  }
}

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
      if (changelogIntent?.worker === next) {
        try {
          sessionStorage.setItem(PWA_CHANGELOG_KEY, JSON.stringify({
            base: WEB_REVISION, userId: changelogIntent.userId, createdAt: Date.now()
          }))
        } catch {
          // Updating must still work without storage.
        }
      }
      changelogIntent = null
      reloading = true
      window.location.reload()
    }
    controller = next
  })
}

export async function requestPwaUpdate({ showChanges = false, userId = null } = {}) {
  const serviceWorker = navigator.serviceWorker
  if (updating) return false
  const registration = await serviceWorker?.getRegistration?.()
  const waiting = registration?.waiting
  if (!waiting) return false

  updating = true
  changelogIntent = showChanges && userId && REVISION_PATTERN.test(WEB_REVISION)
    ? { worker: waiting, userId } : null
  try {
    await new Promise((resolve, reject) => {
      const finish = (error, keepIntent = false) => {
        window.clearTimeout(timer)
        serviceWorker.removeEventListener('controllerchange', onChange)
        waiting.removeEventListener('statechange', onStateChange)
        if (!keepIntent) changelogIntent = null
        if (error) reject(error)
        else resolve()
      }
      const onChange = () => {
        if (serviceWorker.controller === waiting) finish()
      }
      const onStateChange = () => {
        if (waiting.state === 'redundant') finish(new Error('Service worker was replaced.'))
      }
      const timer = window.setTimeout(() => finish(new Error('Service worker activation timed out.'), true), PWA_ACTIVATION_TIMEOUT_MS)
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
