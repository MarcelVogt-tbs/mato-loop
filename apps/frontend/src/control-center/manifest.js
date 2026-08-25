export const controlCenterIntegrationManifest = Object.freeze([
  { id: 'files', label: 'Dateien', icon: 'folder_open' },
  { id: 'mail', label: 'E-Mail', icon: 'mail' },
  { id: 'coding', label: 'OpenCode', icon: 'code' }
])

export const controlCenterGlobalSectionManifest = Object.freeze([
  { id: 'notifications', label: 'Benachrichtigungen', icon: 'notifications' },
  { id: 'approvals', label: 'Freigaben', icon: 'pending_actions' }
])

export const controlCenterIntegrationIds = new Set(controlCenterIntegrationManifest.map((item) => item.id))
export const controlCenterGlobalSectionIds = new Set(controlCenterGlobalSectionManifest.map((item) => item.id))

export function mailControlCenterAvailable(mail) {
  return mail.accountsLoaded && mail.accounts.length > 0
}

export function codingControlCenterAvailable(project) {
  return Boolean(project)
}
