export function buildChatTranscript(messages = [], approvals = [], running = false) {
  const results = new Map()
  for (const message of messages) {
    if (message.role === 'tool' && message.toolCallId) results.set(message.toolCallId, message)
  }

  const approvalsByToolCall = new Map()
  for (const approval of approvals) {
    const toolCallId = approval.details?.toolCallId
    if (toolCallId) approvalsByToolCall.set(toolCallId, approval)
  }

  let lastUserIndex = -1
  messages.forEach((message, index) => {
    if (message.role === 'user') lastUserIndex = index
  })

  const matchedApprovalIds = new Set()
  let activeAssigned = false
  let executionBlocked = false
  const transcriptMessages = messages
    .map((message, index) => ({ message, index }))
    .filter(({ message }) => message.role !== 'tool')
    .map(({ message, index }) => ({
      ...message,
      toolCalls: (message.toolCalls || []).map((tool) => {
        const approval = approvalsByToolCall.get(tool.id) || null
        const result = results.get(tool.id) || null
        const currentRound = running && index > lastUserIndex
        if (approval) matchedApprovalIds.add(approval.id)
        const active = currentRound
          && !result
          && !approval
          && !activeAssigned
          && !executionBlocked
        if (active) activeAssigned = true
        if (currentRound && !result) executionBlocked = true
        return {
          tool,
          result,
          approval,
          active
        }
      })
    }))

  return {
    messages: transcriptMessages,
    unmatchedApprovals: approvals.filter((approval) => !matchedApprovalIds.has(approval.id))
  }
}
