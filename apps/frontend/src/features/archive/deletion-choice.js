import { useUserPreferencesStore } from 'src/features/settings/user-preferences-store'
import DeleteChoiceDialog from './DeleteChoiceDialog.vue'

export async function chooseDeletion($q, label, title, { permanent = false, impact = null } = {}) {
  const settings = useUserPreferencesStore()
  await settings.fetchSettings()
  return new Promise(resolve => {
    $q.dialog({
      component: DeleteChoiceDialog,
      componentProps: {
        label, title, session: Boolean(impact),
        permanentLocked: permanent || !settings.trashEnabled,
        worktree: impact?.worktree || null, hasWorktree: Boolean(impact?.hasWorktree)
      }
    }).onOk(resolve).onCancel(() => resolve(null)).onDismiss(() => resolve(null))
  })
}
