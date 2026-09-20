export const PWA_UPDATE_EVENT = 'matoloop:pwa-update'
export const PWA_UPDATE_RELOAD_KEY = 'matoloop:pwa-update-reload'

export function announcePwaUpdate() {
  window.dispatchEvent(new Event(PWA_UPDATE_EVENT))
}

export function requestPwaUpdate() {
  sessionStorage.setItem(PWA_UPDATE_RELOAD_KEY, '1')
  window.location.reload()
}

export function pwaWindowCount() {
  const controller = navigator.serviceWorker?.controller
  if (!controller) return Promise.resolve(1)

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
      controller.postMessage({ type: 'WINDOW_COUNT' }, [channel.port2])
    } catch {
      finish(null)
    }
  })
}
