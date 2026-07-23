export function chatBottomDistance(target) {
  if (!target) return 0
  return Math.max(0, target.scrollHeight - target.clientHeight - target.scrollTop)
}

export function chatBottomOffset(target) {
  if (!target) return 0
  return Math.max(0, target.scrollHeight - target.clientHeight)
}

export function scrollChatToBottom(target) {
  if (!target) return
  target.scrollTop = chatBottomOffset(target)
}
