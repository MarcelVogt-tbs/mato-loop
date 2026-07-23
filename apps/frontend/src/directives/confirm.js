import { useConfirm } from 'src/composables/confirm'

export default {
  mounted(el, binding) {
    const { confirm } = useConfirm()
    el.__vConfirmValue = binding.value
    el.__vConfirmHandler = async (event) => {
      if (el.__confirmedClick) {
        el.__confirmedClick = false
        return
      }
      event.preventDefault()
      event.stopPropagation()
      const value = typeof el.__vConfirmValue === 'string' ? { message: el.__vConfirmValue } : el.__vConfirmValue || {}
      const accepted = await confirm(value.message, value.title, value)
      if (accepted) {
        el.__confirmedClick = true
        el.click()
      }
    }
    el.addEventListener('click', el.__vConfirmHandler, true)
  },
  updated(el, binding) {
    el.__vConfirmValue = binding.value
  },
  unmounted(el) {
    el.removeEventListener('click', el.__vConfirmHandler, true)
    delete el.__vConfirmValue
    delete el.__vConfirmHandler
    delete el.__confirmedClick
  }
}
