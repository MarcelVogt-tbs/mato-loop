import axios from 'axios'
import { boot } from 'quasar/wrappers'

const api = axios.create({
  baseURL: '/api',
  withCredentials: true
})

export default boot(({ app }) => {
  app.config.globalProperties.$axios = axios
  app.config.globalProperties.$api = api
})

export { api }
