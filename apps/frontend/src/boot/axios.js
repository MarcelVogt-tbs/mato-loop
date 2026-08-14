import axios from 'axios'
import { boot } from 'quasar/wrappers'
import { decodeBlobApiError } from 'src/utils/api-errors'
import { notifyError } from 'src/utils/notifications'

const api = axios.create({
  baseURL: '/api',
  withCredentials: true
})

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (axios.isCancel(error)) return Promise.reject(error)
    const decoded = await decodeBlobApiError(error)
    if (!decoded.config?.suppressErrorToast) notifyError(decoded)
    return Promise.reject(decoded)
  }
)

export default boot(({ app }) => {
  app.config.globalProperties.$axios = axios
  app.config.globalProperties.$api = api
})

export { api }
