export function sessionModel(session, defaults, options, agentId = session?.agentProfileId) {
  const override = session?.sessionProfile?.agentModels?.[agentId] || {}
  return {
    modelId: [override.modelId, session?.modelId, defaults?.defaultModelId]
      .find((id) => options.some((option) => option.value === id)) || null,
    reasoningEffort: override.reasoningEffort || session?.reasoningEffort || defaults?.reasoningEffort || null
  }
}
