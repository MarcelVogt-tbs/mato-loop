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

export async function chooseSessionDeletion($q, title, { permanent = false, impact = {} } = {}) {
  const settings = useUserPreferencesStore()
  await settings.fetchSettings()
  return new Promise(resolve => {
    $q.dialog({
      component: DeleteChoiceDialog,
      componentProps: {
        label: 'Session', title, session: true,
        retentionDays: settings.trashRetentionDays,
        trashEnabled: settings.trashEnabled,
        permanentLocked: permanent || !settings.trashEnabled,
        worktree: impact.worktree || null, hasWorktree: Boolean(impact.hasWorktree)
      }
    }).onOk(resolve).onCancel(() => resolve(null)).onDismiss(() => resolve(null))
  })
}
