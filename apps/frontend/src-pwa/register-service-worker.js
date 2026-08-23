import { register } from 'register-service-worker'

const UPDATE_INTERVAL_MS = 60 * 60 * 1000
let registration = null
let lastUpdateAt = 0

function updateServiceWorker(force = false) {
  if (!registration || (!force && Date.now() - lastUpdateAt < UPDATE_INTERVAL_MS)) return
  lastUpdateAt = Date.now()
  void registration.update().catch(() => {})
}

function useRegistration(value) {
  if (registration) return
  registration = value
  updateServiceWorker(true)
  window.dispatchEvent(new Event('matoloop:service-worker-ready'))
}

register(process.env.SERVICE_WORKER_FILE, {
  registrationOptions: { scope: '/', updateViaCache: 'none' },
  ready: useRegistration,
  registered: useRegistration,
  error(error) {
    console.error('Service worker registration failed.', error)
  }
})

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') updateServiceWorker()
})
