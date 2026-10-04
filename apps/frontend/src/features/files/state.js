export const MAX_WORKSPACE_UPLOADS = 4
export const MAX_WORKSPACE_UPLOAD_BYTES = 64 * 1024 * 1024

export function absoluteComputerPath(path) {
  return typeof path === 'string' && (path.startsWith('/') || /^[A-Z]:\//u.test(path))
}

export function parentPath(path) {
  if (path === '/') return '/'
  if (/^[A-Z]:\/$/u.test(path)) return path
  if (/^[A-Z]:\//u.test(path)) return path.lastIndexOf('/') === 2 ? path.slice(0, 3) : path.slice(0, path.lastIndexOf('/'))
  if (path.startsWith('/')) return path.slice(0, path.lastIndexOf('/')) || '/'
  return path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '.'
}

export function renamedPath(current, source, destination) {
  if (current === source) return destination
  return current?.startsWith(`${source}/`)
    ? `${destination}${current.slice(source.length)}`
    : current
}

export function clearCachedSubtree(store, path) {
  const cachedPaths = new Set([
    ...Object.keys(store.entries),
    ...Object.keys(store.hasMore),
    ...Object.keys(store.nextOffset),
    ...Object.keys(store.loadErrors)
  ])
  for (const key of cachedPaths) {
    if (key === path || key.startsWith(`${path}/`)) {
      delete store.entries[key]
      delete store.hasMore[key]
      delete store.nextOffset[key]
      delete store.loadErrors[key]
    }
  }
}

export function replaceCachedEntry(store, entry) {
  for (const [path, entries] of Object.entries(store.entries)) {
    if (entries.some((item) => item.path === entry.path)) {
      store.entries[path] = entries.map((item) => item.path === entry.path ? entry : item)
    }
  }
}

export function requireWritableContext(store) {
  if (store.toolContext || store.viewedEntry?.revision != null) throw new Error('Historical workspace snapshots are read-only')
}

export function persistedFileView(store) {
  const filesystem = store.scope === 'filesystem'
  return {
    scope: store.scope,
    selectedPath: store.selectedPath,
    selectedKind: store.selectedKind,
    viewedFilePath: store.viewedEntry?.path || null,
    ...(store.scope === 'session' ? { rootIdentity: store.rootIdentity } : {}),
    executionAgentId: filesystem
      ? store.toolContext?.executionAgentId || store.workspaceContext?.executionAgentId || null
      : null,
    currentDirectory: filesystem
      ? store.toolContext?.currentDirectory || store.currentDirectory || null
      : null
  }
}

export function supportsWorkspaceOperation(context, operation) {
  if (
    !context?.executionAgentId
    || context.connected !== true
  ) return false
  const operations = Array.isArray(context.operations)
    ? context.operations
    : Array.isArray(context.capabilities) ? context.capabilities : null
  return operations?.includes(operation) === true
}

export function workspaceContextKey(context) {
  return JSON.stringify([
    context?.executionAgentId || null,
    context?.available,
    context?.connected,
    context?.agentConnected,
    context?.operations || context?.capabilities || null
  ])
}

export function withFileSuffix(name, suffix) {
  const value = String(name || '').trim()
  return value.toLocaleLowerCase().endsWith(suffix) ? value : `${value}${suffix}`
}
