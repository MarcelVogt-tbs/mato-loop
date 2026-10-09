import { boot } from 'quasar/wrappers'
import { applyBranding, useAppearanceStore } from 'src/features/settings/appearance-store'

export default boot(async ({ store }) => {
  const appearance = useAppearanceStore(store)
  try {
    await appearance.fetchAppearance()
  } catch {
    // Keep bundled defaults available when the backend cannot be reached.
  }
  applyBranding(appearance)
})
