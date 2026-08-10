const REVISION = /^[a-f0-9]{64}$/

function stringValue(value) {
  return typeof value === 'string' && value ? value : null
}

export function markdownPrintRequest(query) {
  const path = stringValue(query?.path)
  const scope = stringValue(query?.scope)
  if (!path || !['filesystem', 'session'].includes(scope)) return null

  const params = { path, scope }
  for (const name of ['session_id', 'working_directory', 'root_identity']) {
    const value = stringValue(query[name])
    if (value) params[name] = value
  }
  const revision = stringValue(query.revision)
  return {
    expectedRevision: revision && REVISION.test(revision) ? revision : null,
    params
  }
}
