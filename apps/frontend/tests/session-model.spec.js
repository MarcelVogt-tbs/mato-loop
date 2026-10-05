import { expect, it } from 'vitest'
import { sessionModel } from '../src/features/chat/session-model'

it('resolves each agent override above session and effective defaults', () => {
  const defaults = { defaultModelId: 'global', reasoningEffort: 'low' }
  const session = { modelId: 'session', sessionProfile: { agentModels: { plan: { modelId: 'plan-model', reasoningEffort: 'high' } } } }
  expect([sessionModel(session, defaults, 'plan'), sessionModel(session, defaults, 'build'), sessionModel({}, defaults)]).toEqual([
    { modelId: 'plan-model', reasoningEffort: 'high' }, { modelId: 'session', reasoningEffort: 'low' }, { modelId: 'global', reasoningEffort: 'low' }
  ])
})
