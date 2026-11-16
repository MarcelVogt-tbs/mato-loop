import { useUserPreferencesStore } from 'src/features/settings/user-preferences-store'
import DeleteChoiceDialog from './DeleteChoiceDialog.vue'

export async function chooseDeletion($q, label, title, permanent = false) {
  if (permanent) return true
  const settings = useUserPreferencesStore()
  await settings.fetchSettings()
  if (!settings.trashEnabled) return true
  return new Promise(resolve => {
    $q.dialog({ component: DeleteChoiceDialog, componentProps: { label, title, retentionDays: settings.trashRetentionDays } })
      .onOk(resolve).onCancel(() => resolve(null)).onDismiss(() => resolve(null))
  })
}
