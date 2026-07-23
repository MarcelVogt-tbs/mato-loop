import { Notify } from 'quasar'
import { apiErrorMessage } from 'src/utils/api-errors'

export function notifyError(error, fallback) {
  Notify.create({
    type: 'negative',
    message: apiErrorMessage(error, fallback)
  })
}
