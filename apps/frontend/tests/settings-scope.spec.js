import { describe, expect, it } from 'vitest'

import { resolveSettingsScope } from '../src/features/settings/scope'

describe('settings scope', () => {
  it('defaults to personal unless an administrator explicitly requests instance settings', () => {
    expect(resolveSettingsScope(undefined, true)).toBe('personal')
    expect(resolveSettingsScope('unknown', true)).toBe('personal')
    expect(resolveSettingsScope('personal', true)).toBe('personal')
    expect(resolveSettingsScope('instance', true)).toBe('instance')
    expect(resolveSettingsScope('instance', false)).toBe('personal')
  })
})
