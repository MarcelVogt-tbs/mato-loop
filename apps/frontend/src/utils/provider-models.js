const unavailableStates = new Set(['missing', 'unavailable'])

export function isModelPresent(model) {
  return Boolean(model) && !unavailableStates.has(model.availability)
}

export function isProviderEffective(provider, codex = {}) {
  if (!provider?.enabled) return false
  if (provider.effectiveEnabled === false || provider.runtimeEnabled === false) return false
  return provider.adapterType !== 'openai-codex' || codex.authenticated === true
}

export function isModelEffective(model, provider, codex = {}, runtimeEnabled = true) {
  if (!runtimeEnabled) return false
  if (!model?.enabled || model.effectiveEnabled === false || !isModelPresent(model)) return false
  return isProviderEffective(provider, codex)
}

function effectiveTrustLevel(model, provider) {
  const trustLevel = model?.effectiveTrustLevel
    ?? model?.effective_trust_level
    ?? model?.trustLevel
    ?? provider?.trustLevel
  return trustLevel === 'private' ? 'private' : 'public'
}

export function modelOption(model, provider, disable = false) {
  const trustLevel = effectiveTrustLevel(model, provider)
  return {
    label: `${model.name || model.modelId} · ${provider?.name || 'Provider'}`,
    value: model.id,
    trustLevel,
    trustIcon: trustLevel === 'private' ? 'lock' : 'public',
    disable
  }
}

export function modelSupportsTrust(model, requiredTrustLevel = 'public') {
  return requiredTrustLevel !== 'private' || effectiveTrustLevel(model) === 'private'
}

export function selectedModelState(
  modelId,
  models,
  providers,
  codex = {},
  runtimeEnabled = true
) {
  if (!modelId) {
    return {
      available: false,
      label: 'Kein Modell ausgewählt',
      message: 'Es ist kein verfügbares Standardmodell eingerichtet.'
    }
  }

  const model = models.find((item) => item.id === modelId)
  if (!model) {
    return {
      available: false,
      label: 'Nicht mehr vorhandenes Modell',
      message: 'Das Modell dieser Session ist nicht mehr vorhanden.'
    }
  }

  const provider = providers.find((item) => item.id === model.providerId)
  if (isModelEffective(model, provider, codex, runtimeEnabled)) {
    return { available: true, label: modelOption(model, provider).label, message: '' }
  }

  let reason = 'Das Modell dieser Session ist derzeit nicht verfügbar.'
  if (!runtimeEnabled) reason = 'Die Modell-Laufzeit ist global deaktiviert.'
  else if (!isModelPresent(model)) reason = 'Das Modell dieser Session wurde beim Provider nicht mehr gefunden.'
  else if (!model.enabled) reason = 'Das Modell dieser Session ist deaktiviert.'
  else if (!isProviderEffective(provider, codex)) reason = 'Der Provider dieses Modells ist deaktiviert oder nicht verbunden.'
  return { available: false, label: modelOption(model, provider).label, message: reason }
}

export function discoveryState(provider, syncing = false, failed = false) {
  if (syncing) return 'syncing'
  if (failed) return 'failed'
  return provider?.modelsLastSyncedAt ? 'synced' : 'idle'
}
