const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function applicationServerKey(value) {
  const padding = '='.repeat((4 - value.length % 4) % 4)
  const bytes = atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(bytes, (character) => character.charCodeAt(0))
}

export function pushTargetPath(target) {
  if (target?.type === 'session' && UUID_PATTERN.test(target.sessionId || '')) {
    return `/?session=${encodeURIComponent(target.sessionId)}`
  }
  if (
    target?.type === 'automationRun'
    && UUID_PATTERN.test(target.automationId || '')
    && UUID_PATTERN.test(target.runId || '')
  ) {
    return `/settings/automations/${encodeURIComponent(target.automationId)}?run=${encodeURIComponent(target.runId)}`
  }
  if (target?.type === 'actions') return '/?controlCenter=approvals'
  return '/'
}

export function parsePushPayload(payload) {
  const notification = payload?.version === 1 ? payload.notification : null
  if (!notification || typeof notification.title !== 'string' || !notification.title.trim()) {
    return {
      title: 'Neue Benachrichtigung',
      options: {
        body: 'Öffne MatoLoop, um die Benachrichtigung anzuzeigen.',
        icon: '/icons/icon-192.png',
        data: { version: 1, target: null }
      }
    }
  }
  const target = pushTargetPath(notification.target) === '/' ? null : notification.target
  return {
    title: notification.title,
    options: {
      ...(typeof notification.body === 'string' ? { body: notification.body } : {}),
      ...(typeof notification.tag === 'string' ? { tag: notification.tag } : {}),
      icon: '/icons/icon-192.png',
      data: { version: 1, target }
    }
  }
}
