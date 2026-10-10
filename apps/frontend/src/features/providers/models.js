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
  if (provider.effectiveEnabled === false) return false
  if (provider.adapterType === 'azure-speech' && provider.hasCredential !== true) return false
  return provider.adapterType !== 'openai-codex'
    || provider.hasCredential === true
    || codex.authenticated === true
}

export function isModelEffective(model, provider, codex = {}) {
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
    primaryLabel: model.name || model.modelId,
    caption: provider?.name || 'Provider',
    value: model.id,
    trustLevel,
    trustIcon: trustLevel === 'private' ? 'lock' : 'public',
    disable
  }
}

export function discoveryState(provider, syncing = false, failed = false) {
  if (syncing) return 'syncing'
  if (failed) return 'failed'
  return provider?.modelsLastSyncedAt ? 'synced' : 'idle'
}
