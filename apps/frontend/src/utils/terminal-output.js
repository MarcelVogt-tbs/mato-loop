const PROCESS_TOOLS = new Set(['process_exec', 'process.exec'])
const SHELL_TOOLS = new Set(['shell_run', 'shell.run'])

function shellWord(value) {
  const word = String(value)
  return /^[A-Za-z0-9_./:@%+=,-]+$/.test(word) ? word : `'${word.replaceAll("'", "'\\''")}'`
}

export function terminalCommand(tool) {
  if (SHELL_TOOLS.has(tool?.name) && typeof tool.arguments?.command === 'string') {
    return tool.arguments.command
  }
  if (!PROCESS_TOOLS.has(tool?.name) || typeof tool.arguments?.program !== 'string') return ''
  const args = Array.isArray(tool.arguments.args) ? tool.arguments.args : []
  return [tool.arguments.program, ...args].map(shellWord).join(' ')
}

export function terminalCommandEntries(messages = []) {
  return messages.flatMap((message) => (message?.toolCalls || []).flatMap((tool) => {
    const command = terminalCommand(tool)
    return command && tool.id ? [{ id: tool.id, command }] : []
  }))
}

export function cumulativeOutputDelta(previous = '', current = '') {
  if (typeof current !== 'string' || current === previous || previous.startsWith(current)) return ''
  return current.startsWith(previous) ? current.slice(previous.length) : current
}

export function handleAutoTerminalData(data, stop) {
  if (data !== '\x03') return false
  stop()
  return true
}

export function terminalProposalCommand(proposal) {
  return typeof proposal?.command === 'string' ? proposal.command : ''
}

export function terminalExitStatus(result) {
  if (result?.decision === 'cancelled') return 'Befehl abgebrochen.'
  if (result?.decision && result.decision !== 'execute') return 'Befehl abgelehnt.'
  if (result?.timedOut) return 'Zeitlimit des Befehls überschritten.'
  if (Number.isInteger(result?.exitCode) && result.exitCode < 0) {
    return `Befehl durch Signal ${-result.exitCode} beendet.`
  }
  if (!Number.isInteger(result?.exitCode)) return 'Befehl beendet.'
  return `Befehl mit Status ${result.exitCode} beendet.`
}

export function terminalTabLabel(label, pending) {
  return pending ? `${label}, Befehl wartet auf Bestätigung` : label
}
