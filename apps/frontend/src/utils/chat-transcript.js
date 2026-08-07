export function buildChatTranscript(
  messages = [],
  approvals = [],
  { running = false, toolExecutionActive = running, activeToolCallId = null, questions = [] } = {}
) {
  const results = new Map()
  for (const message of messages) {
    if (message.role !== 'tool' || !message.toolCallId) continue
    const matches = results.get(message.toolCallId) || []
    matches.push(message)
    results.set(message.toolCallId, matches)
  }

  const approvalsByToolCall = new Map()
  for (const approval of approvals) {
    const toolCallId = approval.details?.toolCallId
    if (toolCallId) approvalsByToolCall.set(toolCallId, approval)
  }

  const questionsByToolCall = new Map(questions.map((question) => [question.toolCallId, question]))

  let lastUserIndex = -1
  messages.forEach((message, index) => {
    if (message.role === 'user') lastUserIndex = index
  })

  const matchedApprovalIds = new Set()
  const matchedQuestionIds = new Set()
  let activeAssigned = false
  let executionBlocked = false
  const transcriptMessages = messages
    .map((message, index) => ({ message, index }))
    .filter(({ message }) => message.role !== 'tool')
    .map(({ message, index }) => ({
      ...message,
      _sourceIndex: index,
      toolCalls: (message.toolCalls || []).map((tool, toolIndex) => {
        const currentRound = running && index > lastUserIndex
        const approval = currentRound ? approvalsByToolCall.get(tool.id) || null : null
        const question = currentRound ? questionsByToolCall.get(tool.id) || null : null
        const result = results.get(tool.id)?.shift() || null
        if (approval) matchedApprovalIds.add(approval.id)
        if (question) matchedQuestionIds.add(question.id)
        const active = currentRound && toolExecutionActive && !result && !approval && !question && (activeToolCallId
          ? tool.id === activeToolCallId
          : !activeAssigned && !executionBlocked)
        if (active) activeAssigned = true
        if (currentRound && toolExecutionActive && !result && !activeToolCallId) executionBlocked = true
        return {
          key: `${message.id || index}:${tool.id || toolIndex}:${toolIndex}`,
          tool,
          result,
          approval,
          question,
          current: currentRound,
          active
        }
      }),
      toolHistory: null,
      reasoningHistory: null
    }))

  const groupedMessages = []
  let assistantSection = []

  function flushAssistantSection() {
    if (!assistantSection.length) return
    const toolItems = assistantSection.flatMap((message) => message.toolCalls)
    const reasoningItems = assistantSection.flatMap((message) => message.reasoning ? [message.reasoning] : [])
    const carrier = assistantSection.find((message) => message.toolCalls.length || message.reasoning)
      || assistantSection[0]
    const reasoningCarrier = [...assistantSection].reverse().find((message) => message.reasoning)
      || carrier
    if (toolItems.length) {
      carrier.toolHistory = { id: `tool-history-${carrier.id}`, items: toolItems }
    }
    if (reasoningItems.length) {
      const durations = reasoningItems.map((item) => item.observedDurationMs).filter(Number.isFinite)
      const tokens = reasoningItems.map((item) => item.reasoningTokens).filter(Number.isFinite)
      reasoningCarrier.reasoningHistory = {
        kind: reasoningItems.every((item) => item.kind === 'summary') ? 'summary' : 'content',
        entries: reasoningItems,
        observedDurationMs: durations.length ? durations.reduce((total, value) => total + value, 0) : null,
        reasoningTokens: tokens.length ? tokens.reduce((total, value) => total + value, 0) : null
      }
    }
    const trustLevels = assistantSection.map((message) => message.trustLevel)
    const effectiveTrust = trustLevels.includes('private')
      ? 'private'
      : trustLevels.includes('public') ? 'public' : null
    const reversedSection = [...assistantSection].reverse()
    const finalCreatedAt = reversedSection.find((message) => message.createdAt)?.createdAt || null
    const finalModelName = reversedSection.find((message) => message.modelName)?.modelName || null
    const hasContent = assistantSection.some((message) => Boolean(message.content))
    const currentTurn = running && assistantSection.some((message) => message._sourceIndex > lastUserIndex)

    for (const message of assistantSection) {
      message.toolCalls = []
      message.reasoning = null
      message.createdAt = null
      message.modelName = null
      message.trustLevel = null
    }
    const visibleMessages = assistantSection.filter((message) => (
      message.kind === 'runtime_error'
      || message.content
      || message.generationStatus === 'interrupted'
      || message.attachments?.length
      || message.toolHistory
      || message.reasoningHistory
    ))
    if (visibleMessages.length) {
      visibleMessages[0].showAgentLabel = true
      visibleMessages.forEach((message, index) => {
        message.currentTurn = currentTurn
        message.continuesAssistantTurn = index < visibleMessages.length - 1
      })
      if (!currentTurn) {
        const finalMessage = visibleMessages.at(-1)
        finalMessage.createdAt = finalCreatedAt
        finalMessage.modelName = hasContent ? finalModelName : null
        finalMessage.trustLevel = effectiveTrust
        finalMessage.hasAssistantContent = hasContent
      }
      groupedMessages.push(...visibleMessages)
    }
    for (const message of assistantSection) delete message._sourceIndex
    assistantSection = []
  }

  for (const message of transcriptMessages) {
    if (message.role === 'assistant') {
      assistantSection.push(message)
      continue
    }
    flushAssistantSection()
    delete message._sourceIndex
    groupedMessages.push(message)
  }
  flushAssistantSection()

  return {
    messages: groupedMessages,
    unmatchedApprovals: approvals.filter((approval) => !matchedApprovalIds.has(approval.id)),
    unmatchedQuestions: questions.filter((question) => !matchedQuestionIds.has(question.id))
  }
}
