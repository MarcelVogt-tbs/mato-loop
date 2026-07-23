import axios from 'axios'
import { boot } from 'quasar/wrappers'
import { notifyError } from 'src/utils/notifications'

const api = axios.create({
  baseURL: '/api',
  withCredentials: true
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (!axios.isCancel(error) && !error.config?.suppressErrorToast) notifyError(error)
    return Promise.reject(error)
  }
)

export default boot(({ app }) => {
  app.config.globalProperties.$axios = axios
  app.config.globalProperties.$api = api
})

export { api }
