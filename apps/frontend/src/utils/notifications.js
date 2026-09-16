import { Notify } from 'quasar'
import { apiErrorMessage } from 'src/utils/api-errors'

let backendUnavailableDismiss = null

export function notifyBackendUnavailable() {
  if (backendUnavailableDismiss) return
  backendUnavailableDismiss = Notify.create({
    type: 'negative',
    message: 'MatoLoop ist nicht erreichbar. Die Verbindung wird automatisch geprüft.',
    timeout: 0,
    group: false
  })
}

export function notifyBackendReachable() {
  if (!backendUnavailableDismiss) return
  backendUnavailableDismiss()
  backendUnavailableDismiss = null
}

export function notifyError(error, fallback) {
  Notify.create({
    type: 'negative',
    message: apiErrorMessage(error, fallback)
  })
}
