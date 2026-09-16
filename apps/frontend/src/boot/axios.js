import axios from 'axios'
import { getActivePinia } from 'pinia'
import { boot } from 'quasar/wrappers'
import { decodeBlobApiError, errorDescriptor } from 'src/utils/api-errors'
import {
  notifyBackendReachable,
  notifyBackendUnavailable,
  notifyError
} from 'src/utils/notifications'

const api = axios.create({
  baseURL: '/api',
  withCredentials: true
})

const SESSION_INVALIDATION_CODES = new Set([
  'auth.accountDisabled',
  'auth.notAuthenticated',
  'auth.sessionExpired',
  'auth.userMissing'
])
let sessionInvalidation = null
let adminRefresh = null

export async function recoverControlledAuth(error) {
  const code = errorDescriptor(error)?.code
  const pinia = getActivePinia()
  if (SESSION_INVALIDATION_CODES.has(code)) {
    if (sessionInvalidation?.pinia !== pinia) {
      const request = import('src/stores/auth')
        .then(({ useAuthStore }) => useAuthStore(pinia).invalidateSession())
        .finally(() => {
        if (sessionInvalidation?.request === request) sessionInvalidation = null
      })
      sessionInvalidation = { pinia, request }
    }
    await sessionInvalidation.request
  } else if (code === 'auth.adminRequired' && !error.config?.authRefresh) {
    adminRefresh ||= import('src/stores/auth')
      .then(({ useAuthStore }) => useAuthStore(pinia).fetchMe({
        authRefresh: true,
        suppressErrorToast: true
      }))
      .catch(() => {})
      .finally(() => { adminRefresh = null })
    await adminRefresh
  }
}

api.interceptors.response.use(
  (response) => {
    notifyBackendReachable()
    return response
  },
  async (error) => {
    if (axios.isCancel(error)) return Promise.reject(error)
    const decoded = await decodeBlobApiError(error)
    await recoverControlledAuth(decoded)
    const backendUnavailable = (decoded.request && !decoded.response)
      || (!errorDescriptor(decoded) && [502, 503, 504].includes(decoded.response?.status))
    if (backendUnavailable) notifyBackendUnavailable()
    else {
      notifyBackendReachable()
      if (!decoded.config?.suppressErrorToast) notifyError(decoded)
    }
    return Promise.reject(decoded)
  }
)

export default boot(({ app }) => {
  app.config.globalProperties.$axios = axios
  app.config.globalProperties.$api = api
})

export { api }
