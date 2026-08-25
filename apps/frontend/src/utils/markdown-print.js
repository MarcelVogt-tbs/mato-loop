const REVISION = /^[a-f0-9]{64}$/

function stringValue(value) {
  return typeof value === 'string' && value ? value : null
}

export function markdownPrintRequest(query) {
  const path = stringValue(query?.path)
  const scope = stringValue(query?.scope)
  if (!path || !['filesystem', 'session'].includes(scope)) return null

  const params = { path, scope }
  const sessionId = stringValue(query.session_id)
  const rootIdentity = stringValue(query.root_identity)
  if (scope === 'session') {
    if (!sessionId) return null
    params.session_id = sessionId
    if (rootIdentity) params.root_identity = rootIdentity
  } else {
    const executionAgentId = stringValue(query.execution_agent_id)
    const currentDirectory = stringValue(query.current_directory)
    if (!executionAgentId || !currentDirectory || !path.startsWith('/')) return null
    if (sessionId) params.session_id = sessionId
    params.execution_agent_id = executionAgentId
    params.current_directory = currentDirectory
  }
  const revision = stringValue(query.revision)
  return {
    expectedRevision: revision && REVISION.test(revision) ? revision : null,
    params
  }
}
