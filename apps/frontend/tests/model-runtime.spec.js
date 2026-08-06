import { describe, expect, it } from 'vitest'

import { DEFAULT_MODEL_RUNTIME, modelRuntimeForm, modelRuntimePayload } from '../src/utils/model-runtime'

describe('model runtime configuration', () => {
  it('uses safe defaults for a new model', () => {
    expect(modelRuntimeForm()).toEqual(DEFAULT_MODEL_RUNTIME)
  })

  it('reads camelCase API fields and preserves false capabilities', () => {
    expect(modelRuntimeForm({
      capabilities: ['chat'],
      inputModalities: ['text', 'image'],
      contextWindowTokens: 64000,
      discoveredContextWindowTokens: 32000,
      useDiscoveredContextWindow: false,
      contextWindowSource: 'override',
      maxOutputTokens: 8000,
      charactersPerToken: 3.5,
      supportsTools: false,
      supportsParallelToolCalls: false,
      supportsStreaming: false,
      supportsImageInput: true,
      trustLevel: null,
      pseudonymizationEnabled: false,
      memoryPolicy: 'deny',
      webfetchPolicy: 'auto_public',
      effectiveWebfetchPolicy: 'disabled',
      reasoningSummary: false
    })).toEqual({
      capabilities: ['chat'],
      inputModalities: ['text', 'image'],
      contextWindowTokens: 64000,
      discoveredContextWindowTokens: 32000,
      useDiscoveredContextWindow: false,
      contextWindowSource: 'override',
      maxOutputTokens: 8000,
      charactersPerToken: 3.5,
      supportsTools: false,
      supportsParallelToolCalls: false,
      supportsStreaming: false,
      supportsImageInput: true,
      trustLevel: null,
      pseudonymizationEnabled: false,
      memoryPolicy: 'deny',
      webfetchPolicy: 'auto_public',
      effectiveWebfetchPolicy: 'disabled',
      reasoningSummary: false
    })
  })

  it('maps form values to additive provider request fields', () => {
    expect(modelRuntimePayload({
      capabilities: ['chat', 'image_generation'],
      inputModalities: ['text', 'image', 'audio', 'video'],
      contextWindowTokens: '32000',
      useDiscoveredContextWindow: true,
      maxOutputTokens: '4096',
      charactersPerToken: '4.2',
      supportsTools: false,
      supportsParallelToolCalls: true,
      supportsStreaming: true,
      supportsImageInput: true,
      trustLevel: 'private',
      pseudonymizationEnabled: null,
      memoryPolicy: 'allow',
      webfetchPolicy: 'auto_allowed',
      reasoningSummary: false
    })).toEqual({
      capabilities: ['chat', 'image_generation'],
      input_modalities: ['text', 'image', 'audio', 'video'],
      context_window_tokens: 32000,
      use_discovered_context_window: true,
      max_output_tokens: 4096,
      characters_per_token: 4.2,
      supports_tools: false,
      supports_parallel_tool_calls: false,
      supports_streaming: true,
      supports_image_input: true,
      trust_level: 'private',
      pseudonymization_enabled: null,
      memory_policy: 'allow',
      webfetch_policy: 'auto_allowed',
      reasoning_summary: false
    })
  })

  it('preserves inherited model policy values', () => {
    expect(modelRuntimeForm({
      trustLevel: null,
      pseudonymizationEnabled: null
    })).toMatchObject({
      trustLevel: null,
      pseudonymizationEnabled: null
    })
  })

  it('preserves media capabilities without inferring them from image input support', () => {
    expect(modelRuntimeForm({
      capabilities: ['audio_transcription'],
      supportsImageInput: true
    })).toMatchObject({
      capabilities: ['audio_transcription'],
      inputModalities: ['text', 'image'],
      supportsImageInput: true
    })

    expect(modelRuntimePayload({
      ...modelRuntimeForm(),
      capabilities: ['speech_generation'],
      supportsImageInput: false
    })).toMatchObject({
      capabilities: ['speech_generation'],
      supports_image_input: false
    })

    expect(modelRuntimeForm({ capabilities: [] }).capabilities).toEqual([])
    expect(modelRuntimePayload({
      ...modelRuntimeForm(),
      capabilities: []
    }).capabilities).toEqual([])
  })

  it('uses canonical input modalities while keeping the legacy image API field', () => {
    expect(modelRuntimeForm({
      inputModalities: ['video', 'image', 'invalid', 'video'],
      supportsImageInput: false
    })).toMatchObject({
      inputModalities: ['video', 'image'],
      supportsImageInput: true
    })
    expect(modelRuntimePayload({
      ...modelRuntimeForm(),
      inputModalities: ['text', 'audio', 'video'],
      supportsImageInput: true
    }).supports_image_input).toBe(false)
    expect(modelRuntimePayload({
      ...modelRuntimeForm(),
      inputModalities: undefined,
      supportsImageInput: true
    }).supports_image_input).toBe(true)
  })
})
