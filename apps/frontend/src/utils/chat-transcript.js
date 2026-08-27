const CONTEXT_TOOL_NAMES = new Set(['read', 'read_file', 'glob', 'grep', 'list_directory'])

export function retryAvailableForMessage(message, messages = [], running = false) {
  return message?.kind === 'runtime_error'
    && message.retryAvailable === true
    && messages.at(-1)?.id === message.id
    && !running
}

export function withLiveTranscriptMessage(messages, liveMessage) {
  if (liveMessage.content) return [...messages, liveMessage]
  const lastUserIndex = messages.reduce((found, message, index) => (
    message.role === 'user' ? index : found
  ), -1)
  const currentIndex = messages.reduce((found, message, index) => (
    message.role === 'assistant' && index > lastUserIndex ? index : found
  ), -1)
  if (currentIndex >= 0) {
    const displayed = [...messages]
    displayed[currentIndex] = {
      ...displayed[currentIndex],
      ...(liveMessage.workHistory ? { workHistory: liveMessage.workHistory } : {}),
      liveMessageId: liveMessage.id
    }
    return displayed
  }
  return liveMessage.workHistory ? [...messages, liveMessage] : messages
}

export function groupWorkHistoryEntries(entries = []) {
  const grouped = []
  for (const entry of entries) {
    if (entry.type !== 'tool' || !CONTEXT_TOOL_NAMES.has(entry.tool?.name)) {
      grouped.push(entry)
      continue
    }
    const previous = grouped.at(-1)
    if (previous?.type === 'context') {
      previous.items.push(entry)
      continue
    }
    grouped.push({ key: `context-${entry.key}`, type: 'context', items: [entry] })
  }
  return grouped
}

export function splitLiveWorkHistory(entries = [], activity = null) {
  const attention = entries.find((entry) => (
    entry.type === 'tool' && (entry.approval || entry.question)
  ))
  const liveReasoning = activity?.phase === 'waiting_model'
    ? [...entries].reverse().find((entry) => entry.type === 'reasoning' && entry.live)
    : null
  const currentContextSummary = ['compacting_context', 'waiting_model'].includes(activity?.phase)
    ? [...entries].reverse().find((entry) => entry.type === 'context-summary' && entry.current)
    : null
  const activeTool = activity?.phase === 'processing_tool'
    ? entries.find((entry) => entry.type === 'tool' && (
      entry.active || entry.tool?.id === activity.toolCallId
    ))
    : null
  const current = attention || liveReasoning || currentContextSummary || activeTool || null
  const previous = entries.filter((entry) => (
    entry !== current
    && !entry.live
    && (entry.type !== 'tool' || entry.result || entry.approval || entry.question)
  ))
  return { current, previous }
}

export function buildChatTranscript(
  messages = [],
  approvals = [],
  {
    running = false,
    toolExecutionActive = running,
    activeToolCallId = null,
    processOutputs = {},
    questions = [],
    contextSummaries = []
  } = {}
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
          active,
          ...(processOutputs[tool.id] ? { processOutput: processOutputs[tool.id] } : {})
        }
      }),
      workHistory: null
    }))
  const latestUserTime = Date.parse(messages[lastUserIndex]?.createdAt || '')
  const summaryMessages = contextSummaries.map((summary) => {
    const summaryTime = Date.parse(summary.createdAt || '')
    return {
      id: `context-summary-${summary.id}`,
      role: 'assistant',
      kind: 'context_summary',
      content: '',
      toolCalls: [],
      reasoning: null,
      createdAt: summary.createdAt,
      coveredThroughMessageId: summary.coveredThroughMessageId,
      contextSummaryEntry: {
        key: `context-summary-${summary.id}`,
        type: 'context-summary',
        text: summary.content,
        warning: summary.isFallback,
        current: running
          && Number.isFinite(summaryTime)
          && Number.isFinite(latestUserTime)
          && summaryTime >= latestUserTime,
        presentation: {
          title: 'Gesprächskontext verdichtet',
          summary: summary.isFallback ? 'Vereinfachte Ersatzfassung' : null,
          duration: null
        }
      },
      _sourceIndex: -1,
      _timelineId: summary.id
    }
  }).sort((left, right) => {
    const leftTime = Date.parse(left.createdAt || '')
    const rightTime = Date.parse(right.createdAt || '')
    return leftTime - rightTime || left.id.localeCompare(right.id)
  })
  for (const summary of summaryMessages) {
    const summaryTime = Date.parse(summary.createdAt || '')
    const index = transcriptMessages.findIndex((message) => {
      const messageTime = Date.parse(message.createdAt || '')
      if (!Number.isFinite(summaryTime) || !Number.isFinite(messageTime)) return false
      const messageId = String(message._timelineId || message.id)
      return summaryTime < messageTime
        || (summaryTime === messageTime && summary._timelineId < messageId)
    })
    if (index < 0) transcriptMessages.push(summary)
    else transcriptMessages.splice(index, 0, summary)
  }

  const groupedMessages = []
  let assistantSection = []
  let turnStartedAt = null

  function flushAssistantSection() {
    if (!assistantSection.length) return
    const workEntries = assistantSection.flatMap((message) => [
      ...(message.contextSummaryEntry ? [message.contextSummaryEntry] : []),
      ...(message.reasoning
        ? [{ key: `reasoning-${message.id || message._sourceIndex}`, type: 'reasoning', reasoning: message.reasoning }]
        : []),
      ...(message.content && message.toolCalls.length
        ? [{ key: `note-${message.id || message._sourceIndex}`, type: 'note', text: message.content }]
        : []),
      ...message.toolCalls.map((item) => ({ ...item, type: 'tool' }))
    ])
    const reasoningItems = workEntries
      .filter((entry) => entry.type === 'reasoning')
      .map((entry) => entry.reasoning)
    const carrier = assistantSection.find((message) => (
      message.toolCalls.length || message.reasoning || message.contextSummaryEntry
    ))
      || assistantSection[0]
    const reversedSection = [...assistantSection].reverse()
    const finalCreatedAt = reversedSection.find((message) => message.createdAt)?.createdAt || null
    const currentTurn = running && assistantSection.some((message) => (
      message._sourceIndex > lastUserIndex || message.contextSummaryEntry?.current
    ))
    if (workEntries.length) {
      const durations = reasoningItems.map((item) => item.observedDurationMs).filter(Number.isFinite)
      carrier.workHistory = {
        id: `work-history-${carrier.id}`,
        entries: workEntries,
        observedDurationMs: durations.length ? durations.reduce((total, value) => total + value, 0) : null,
        startedAt: turnStartedAt,
        completedAt: currentTurn ? null : finalCreatedAt
      }
    }
    const trustLevels = assistantSection.map((message) => message.trustLevel)
    const effectiveTrust = trustLevels.includes('private')
      ? 'private'
      : trustLevels.includes('public') ? 'public' : null
    const finalModelName = reversedSection.find((message) => message.modelName)?.modelName || null
    const hasContent = assistantSection.some((message) => Boolean(message.content))

    for (const message of assistantSection) {
      if (message.toolCalls.length) message.content = ''
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
      || message.workHistory
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
    for (const message of assistantSection) {
      delete message._sourceIndex
      delete message._timelineId
    }
    assistantSection = []
  }

  for (const message of transcriptMessages) {
    if (message.role === 'assistant') {
      if (message.sourceType === 'automationRun') flushAssistantSection()
      assistantSection.push(message)
      continue
    }
    flushAssistantSection()
    delete message._sourceIndex
    delete message._timelineId
    groupedMessages.push(message)
    if (message.role === 'user') turnStartedAt = message.createdAt || null
  }
  flushAssistantSection()

  return {
    messages: groupedMessages,
    unmatchedApprovals: approvals.filter((approval) => !matchedApprovalIds.has(approval.id)),
    unmatchedQuestions: questions.filter((question) => !matchedQuestionIds.has(question.id))
  }
}
