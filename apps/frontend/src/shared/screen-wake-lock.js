export function createScreenWakeLock(onChange = () => {}) {
  let wakeLock = null
  let closed = false

  function releaseLock(lock) {
    void lock.release().catch(() => console.warn('Screen wake lock could not be released.'))
  }

  return {
    async request() {
      if (closed) return
      if (!globalThis.navigator?.wakeLock?.request) { onChange(false); return }
      try {
        const lock = await navigator.wakeLock.request('screen')
        if (closed) { releaseLock(lock); return }
        wakeLock = lock
        lock.addEventListener('release', () => {
          wakeLock = null
          if (!closed) onChange(false)
        })
        onChange(!lock.released)
      } catch {
        if (!closed) onChange(false)
      }
    },
    release() {
      closed = true
      if (wakeLock) { releaseLock(wakeLock); wakeLock = null }
    }
  }
}
