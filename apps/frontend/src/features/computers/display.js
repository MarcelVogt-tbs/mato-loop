export function agentDisplayName(agent) {
  if (!agent) return ''
  return agent.displayName || agent.alias?.trim() || agent.computerName?.trim()
    || (agent.id ? `Computer ${agent.id.slice(0, 8)}` : 'Computer')
}
