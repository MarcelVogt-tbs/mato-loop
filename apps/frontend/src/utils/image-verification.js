export const IMAGE_VERIFICATION_TIMEOUT_MS = 15_000

export function verifyExternalImage(url, options = {}) {
  const createImage = options.createImage || (() => new Image())
  const scheduleTimeout = options.scheduleTimeout || window.setTimeout.bind(window)
  const cancelTimeout = options.cancelTimeout || window.clearTimeout.bind(window)
  const timeoutMs = options.timeoutMs ?? IMAGE_VERIFICATION_TIMEOUT_MS
  const image = createImage()
  let timeoutId = null
  let settled = false
  let resolveResult

  const promise = new Promise((resolve) => {
    resolveResult = resolve
  })

  function finish(status, reason = null) {
    if (settled) return
    settled = true
    if (timeoutId !== null) cancelTimeout(timeoutId)
    image.onload = null
    image.onerror = null
    resolveResult({ status, reason })
  }

  image.referrerPolicy = 'no-referrer'
  image.decoding = 'async'
  image.onload = async () => {
    try {
      if (typeof image.decode === 'function') await image.decode()
      finish('ready')
    } catch {
      finish('failed', 'decode')
    }
  }
  image.onerror = () => finish('failed', 'load')
  timeoutId = scheduleTimeout(() => {
    finish('failed', 'timeout')
    image.removeAttribute?.('src')
  }, timeoutMs)
  image.src = url

  return {
    promise,
    cancel() {
      if (settled) return
      finish('cancelled')
      image.removeAttribute?.('src')
    }
  }
}
