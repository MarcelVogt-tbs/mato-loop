export function resolveSettingsScope(requestedScope, isAdmin) {
  return requestedScope === 'instance' && isAdmin ? 'instance' : 'personal'
}
