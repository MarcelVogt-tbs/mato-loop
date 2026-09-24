export function searchShortcutAction(event, dialogOpen) {
  if (
    event.defaultPrevented
    || event.repeat
    || event.altKey
    || event.shiftKey
    || (!event.ctrlKey && !event.metaKey)
    || event.key?.toLowerCase() !== 'f'
  ) return null
  return dialogOpen ? 'browser' : 'app'
}
