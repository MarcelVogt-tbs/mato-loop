import { boot } from 'quasar/wrappers'
import vConfirm from 'src/directives/confirm'

export default boot(({ app }) => {
  app.directive('confirm', vConfirm)
})
