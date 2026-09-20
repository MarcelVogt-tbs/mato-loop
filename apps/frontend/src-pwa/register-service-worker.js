import { register } from 'register-service-worker'
import {
  announcePwaUpdate,
  PWA_UPDATE_RELOAD_KEY
} from '../src/utils/pwa-update'

const UPDATE_INTERVAL_MS = 60 * 60 * 1000
let registration = null
let lastUpdateAt = 0

function updateServiceWorker(force = false) {
  if (!registration || (!force && Date.now() - lastUpdateAt < UPDATE_INTERVAL_MS)) return
  lastUpdateAt = Date.now()
  void registration.update().catch(() => { lastUpdateAt = 0 })
}

function activateRequestedUpdate() {
  if (sessionStorage.getItem(PWA_UPDATE_RELOAD_KEY) !== '1') return false
  if (!registration.waiting) {
    sessionStorage.removeItem(PWA_UPDATE_RELOAD_KEY)
    return false
  }

  let reloading = false
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (reloading) return
    reloading = true
    sessionStorage.removeItem(PWA_UPDATE_RELOAD_KEY)
    window.location.reload()
  })
  registration.waiting.postMessage({ type: 'SKIP_WAITING' })
  return true
}

function useRegistration(value) {
  registration ||= value
  if (activateRequestedUpdate()) return
  if (registration.waiting) announcePwaUpdate()
  updateServiceWorker()
  window.dispatchEvent(new Event('matoloop:service-worker-ready'))
}

register(process.env.SERVICE_WORKER_FILE, {
  registrationOptions: { scope: '/', updateViaCache: 'none' },
  ready: useRegistration,
  registered: useRegistration,
  updated(value) {
    registration ||= value
    if (!activateRequestedUpdate()) announcePwaUpdate()
  },
  error(error) {
    console.error('Service worker registration failed.', error)
  }
})

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') updateServiceWorker()
})
window.addEventListener('online', () => updateServiceWorker(true))
