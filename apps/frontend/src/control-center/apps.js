import ApprovalCenterApp from 'components/ApprovalCenterApp.vue'
import FileExplorerPanel from 'components/FileExplorerPanel.vue'
import NotificationCenterApp from 'components/NotificationCenterApp.vue'
import MailApp from 'components/MailApp.vue'
import CodingApp from 'components/CodingApp.vue'
import {
  controlCenterGlobalSectionManifest,
  controlCenterIntegrationManifest
} from 'src/control-center/manifest'

const components = {
  files: FileExplorerPanel,
  mail: MailApp,
  coding: CodingApp,
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
