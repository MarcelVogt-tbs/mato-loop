import { describe, expect, it } from 'vitest'

import {
  formatByteSize,
  formatStorageUsage,
  modelContextPercentage,
  storageUsageProgress
} from '../src/utils/resource-metrics'

describe('resource metrics', () => {
  it('formats storage compactly with de-DE units through gigabytes', () => {
    expect(formatStorageUsage({
      usedBytes: 13 * 1024 * 1024,
      quotaBytes: 500 * 1024 * 1024
    })).toBe('13 MB / 500 MB')
    expect(formatByteSize(1.5 * 1024 ** 3)).toBe('1,5 GB')
    expect(formatStorageUsage({ usedBytes: 12 * 1024 ** 2, quotaBytes: 100 * 1024 ** 3 }))
      .toBe('12 MB / 100 GB')
  })

  it('calculates bounded storage utilization', () => {
    expect(storageUsageProgress({ usedBytes: 125, quotaBytes: 500 })).toBe(0.25)
    expect(storageUsageProgress({ usedBytes: 600, quotaBytes: 500 })).toBe(1)
    expect(storageUsageProgress({ usedBytes: 0, quotaBytes: 0 })).toBeNull()
  })

  it('shows unlimited storage without an utilization bar', () => {
    const usage = { usedBytes: 12 * 1024 ** 2, quotaBytes: null }
    expect([formatStorageUsage(usage), storageUsageProgress(usage)])
      .toEqual(['12 MB belegt · unbegrenzt', null])
  })

  it('calculates the latest estimated model input utilization', () => {
    expect(modelContextPercentage({ estimatedInputTokens: 333, inputBudgetTokens: 1000 })).toBe(33)
    expect(modelContextPercentage({ estimatedInputTokens: 1200, inputBudgetTokens: 1000 })).toBe(120)
    expect(modelContextPercentage({ estimatedInputTokens: 0, inputBudgetTokens: 0 })).toBeNull()
    expect(modelContextPercentage({ estimatedInputTokens: '333', inputBudgetTokens: 1000 })).toBeNull()
  })
})
