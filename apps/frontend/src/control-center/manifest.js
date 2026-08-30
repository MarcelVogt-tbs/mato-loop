export const controlCenterIntegrationManifest = Object.freeze([
  { id: 'files', label: 'Dateien', icon: 'folder_open' },
  { id: 'mail', label: 'E-Mail', icon: 'mail' },
  { id: 'coding', label: 'OpenCode', icon: 'code' },
  { id: 'terminal', label: 'Terminal', icon: 'terminal' }
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

export const TERMINAL_OPERATIONS = Object.freeze([
  'terminal.open',
  'terminal.read',
  'terminal.write',
  'terminal.resize',
  'terminal.close',
  'terminal.run'
])

export function terminalControlCenterState(context, executionAgents) {
  const agentId = context?.preferredExecutionAgentId
  if (!agentId) return { available: false, reason: 'missing', agent: null }
  const agent = executionAgents.byId(agentId)
  if (!agent) return { available: false, reason: 'missing', agent: null }
  if (!agent.connected) return { available: false, reason: 'offline', agent }
  const available = TERMINAL_OPERATIONS.every((operation) => agent.operations?.includes(operation))
  return { available, reason: available ? null : 'unsupported', agent }
}
