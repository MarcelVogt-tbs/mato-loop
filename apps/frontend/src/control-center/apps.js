import AgentCodingApp from 'components/AgentCodingApp.vue'
import ComputerFileExplorerPanel from 'components/ComputerFileExplorerPanel.vue'
import FileExplorerPanel from 'components/FileExplorerPanel.vue'
import FileExplorerFooter from 'components/FileExplorerFooter.vue'
import FileViewerApp from 'components/FileViewerApp.vue'
import FileViewerFooter from 'components/FileViewerFooter.vue'
import NotificationCenterApp from 'components/NotificationCenterApp.vue'
import MailApp from 'components/MailApp.vue'
import CodingApp from 'components/CodingApp.vue'
import SessionApp from 'components/SessionApp.vue'
import SessionFooter from 'components/SessionFooter.vue'
import TerminalApp from 'components/TerminalApp.vue'
import TerminalFooter from 'components/TerminalFooter.vue'
import {
  controlCenterGlobalSectionManifest,
  controlCenterIntegrationManifest
} from 'src/control-center/manifest'

const components = {
  session: SessionApp,
  'agent-coding': AgentCodingApp,
  files: FileExplorerPanel,
  'computer-files': ComputerFileExplorerPanel,
  'file-viewer': FileViewerApp,
  mail: MailApp,
  coding: CodingApp,
  terminal: TerminalApp,
  notifications: NotificationCenterApp
}

const footerComponents = {
  session: SessionFooter,
  files: FileExplorerFooter,
  'file-viewer': FileViewerFooter,
  terminal: TerminalFooter
}

function withComponents(items) {
  return items.map((item) => ({
    ...item,
    component: components[item.id],
    footerComponent: footerComponents[item.id] || null
  }))
}

export const controlCenterIntegrations = Object.freeze(withComponents(controlCenterIntegrationManifest))
export const controlCenterGlobalSections = Object.freeze(withComponents(controlCenterGlobalSectionManifest))
export const controlCenterContents = Object.freeze([
  ...controlCenterIntegrations,
  ...controlCenterGlobalSections
])
export const controlCenterContentsById = new Map(controlCenterContents.map((item) => [item.id, item]))
