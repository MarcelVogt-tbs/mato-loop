export function sessionModel(session, defaults, agentId = session?.agentProfileId) {
  const override = session?.sessionProfile?.agentModels?.[agentId] || {}
  return {
    modelId: override.modelId || session?.modelId || defaults?.defaultModelId || null,
    reasoningEffort: override.reasoningEffort || session?.reasoningEffort || defaults?.reasoningEffort || null
  }
}
