import ApprovalCenterApp from 'components/ApprovalCenterApp.vue'
import FileExplorerPanel from 'components/FileExplorerPanel.vue'
import NotificationCenterApp from 'components/NotificationCenterApp.vue'
import {
  controlCenterGlobalSectionManifest,
  controlCenterIntegrationManifest
} from 'src/control-center/manifest'

const components = {
  files: FileExplorerPanel,
  notifications: NotificationCenterApp,
  approvals: ApprovalCenterApp
}

function withComponents(items) {
  return items.map((item) => ({
    ...item,
    component: components[item.id]
  }))
}

export const controlCenterIntegrations = Object.freeze(withComponents(controlCenterIntegrationManifest))
export const controlCenterGlobalSections = Object.freeze(withComponents(controlCenterGlobalSectionManifest))
export const controlCenterContents = Object.freeze([
  ...controlCenterIntegrations,
  ...controlCenterGlobalSections
])
export const controlCenterContentsById = new Map(controlCenterContents.map((item) => [item.id, item]))
