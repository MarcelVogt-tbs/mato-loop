import AgentCodingApp from 'src/features/coding/AgentCodingApp.vue'
import ComputerFileExplorerPanel from 'src/features/files/components/ComputerFileExplorerPanel.vue'
import FileExplorerPanel from 'src/features/files/components/FileExplorerPanel.vue'
import FileExplorerFooter from 'src/features/files/components/FileExplorerFooter.vue'
import FileViewerApp from 'src/features/files/components/FileViewerApp.vue'
import FileViewerFooter from 'src/features/files/components/FileViewerFooter.vue'
import NotificationCenterApp from 'src/features/notifications/NotificationCenterApp.vue'
import MailApp from 'src/features/mail/MailApp.vue'
import TbsDeskApp from 'src/features/tbs-desk/TbsDeskApp.vue'
import CodingApp from 'src/features/coding/CodingApp.vue'
import SessionApp from 'src/features/chat/components/SessionApp.vue'
import SessionFooter from 'src/features/chat/components/SessionFooter.vue'
import TerminalApp from 'src/features/terminal/TerminalApp.vue'
import TerminalFooter from 'src/features/terminal/TerminalFooter.vue'
import BrowserApp from 'src/features/browser/BrowserApp.vue'
import BrowserFooter from 'src/features/browser/BrowserFooter.vue'
import {
  controlCenterGlobalSectionManifest,
  controlCenterIntegrationManifest
} from 'src/app/control-center/manifest'

const components = {
  session: SessionApp,
  'agent-coding': AgentCodingApp,
  files: FileExplorerPanel,
  'computer-files': ComputerFileExplorerPanel,
  'file-viewer': FileViewerApp,
  mail: MailApp,
  'tbs-desk': TbsDeskApp,
  coding: CodingApp,
  terminal: TerminalApp,
  browser: BrowserApp,
  notifications: NotificationCenterApp
}

const footerComponents = {
  session: SessionFooter,
  files: FileExplorerFooter,
  'file-viewer': FileViewerFooter,
  terminal: TerminalFooter,
  browser: BrowserFooter
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
