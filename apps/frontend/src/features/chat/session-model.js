export function sessionModel(session, providers, agentId = session?.agentProfileId) {
  const override = session?.sessionProfile?.agentModels?.[agentId] || {}
  const defaults = providers.modelDefaults
  const profileOverride = defaults?.profileModels?.[agentId] || {}
  const candidates = [override.modelId, session?.modelId, profileOverride.modelId, defaults?.defaultModelId]
  const options = providers.selectionOptions(null, true)
  const modelId = candidates.find((id) => options.some((option) => option.value === id && !option.disable)) || null
  let reasoningEffort = override.reasoningEffort || session?.reasoningEffort || profileOverride.reasoningEffort || defaults?.reasoningEffort || null
  if (modelId !== candidates.find(Boolean) || !(override.reasoningEffort || session?.reasoningEffort)) {
    const model = (providers.availableModels || providers.models).find((item) => item.id === modelId)
    if (!model?.reasoningEfforts?.includes(reasoningEffort)) reasoningEffort = null
  }
  return { modelId, reasoningEffort }
}
