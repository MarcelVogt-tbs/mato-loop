const unavailableStates = new Set(['missing', 'unavailable'])

export const MODEL_CAPABILITY_OPTIONS = Object.freeze([
  { label: 'Chat', value: 'chat', icon: 'chat' },
  { label: 'Bildgenerierung', value: 'image_generation', icon: 'image' },
  { label: 'Bildbearbeitung', value: 'image_editing', icon: 'edit' },
  { label: 'Sprachausgabe', value: 'speech_generation', icon: 'record_voice_over' },
  { label: 'Audio und Musik', value: 'audio_generation', icon: 'music_note' },
  { label: 'Transkription', value: 'audio_transcription', icon: 'graphic_eq' }
])

export function normalizeModelCapabilities(capabilities) {
  if (!Array.isArray(capabilities)) return ['chat']
  return [...new Set(capabilities.filter((capability) => typeof capability === 'string'))]
}

export function modelSupportsCapability(model, capability = 'chat') {
  return normalizeModelCapabilities(model?.capabilities).includes(capability)
}

export function modelCapabilityLabel(capability) {
  return MODEL_CAPABILITY_OPTIONS.find((option) => option.value === capability)?.label || capability
}

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
  runtimeEnabled = true,
  capability = 'chat'
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
  if (isModelEffective(model, provider, codex, runtimeEnabled) && modelSupportsCapability(model, capability)) {
    return { available: true, label: modelOption(model, provider).label, message: '' }
  }

  let reason = 'Das Modell dieser Session ist derzeit nicht verfügbar.'
  if (!runtimeEnabled) reason = 'Die Modell-Laufzeit ist global deaktiviert.'
  else if (!isModelPresent(model)) reason = 'Das Modell dieser Session wurde beim Provider nicht mehr gefunden.'
  else if (!model.enabled) reason = 'Das Modell dieser Session ist deaktiviert.'
  else if (!isProviderEffective(provider, codex)) reason = 'Der Provider dieses Modells ist deaktiviert oder nicht verbunden.'
  else if (!modelSupportsCapability(model, capability)) {
    reason = capability === 'chat'
      ? 'Das Modell dieser Session unterstützt keinen Chat.'
      : 'Das Modell unterstützt die benötigte Fähigkeit nicht.'
  }
  return { available: false, label: modelOption(model, provider).label, message: reason }
}

export function discoveryState(provider, syncing = false, failed = false) {
  if (syncing) return 'syncing'
  if (failed) return 'failed'
  return provider?.modelsLastSyncedAt ? 'synced' : 'idle'
}
