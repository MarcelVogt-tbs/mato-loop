const DECIMAL_FORMATTER = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })
const INTEGER_FORMATTER = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })

const BYTE_UNITS = [
  { bytes: 1024 ** 3, label: 'GB' },
  { bytes: 1024 ** 2, label: 'MB' },
  { bytes: 1024, label: 'KB' },
  { bytes: 1, label: 'B' }
]

function validAmount(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function byteUnit(value) {
  return BYTE_UNITS.find((unit) => Number(value) >= unit.bytes) || BYTE_UNITS.at(-1)
}

export function formatMetricInteger(value) {
  return validAmount(value) ? INTEGER_FORMATTER.format(Number(value)) : null
}

export function formatByteSize(value) {
  if (!validAmount(value)) return null
  const unit = byteUnit(value)
  return `${DECIMAL_FORMATTER.format(Number(value) / unit.bytes)} ${unit.label}`
}

export function formatStorageUsage(usage) {
  if (!validAmount(usage?.usedBytes) || !validAmount(usage?.quotaBytes)) return null
  return `${formatByteSize(usage.usedBytes)} / ${formatByteSize(usage.quotaBytes)}`
}

export function storageUsageProgress(usage) {
  if (!validAmount(usage?.usedBytes) || !validAmount(usage?.quotaBytes) || usage.quotaBytes === 0) return null
  return Math.min(usage.usedBytes / usage.quotaBytes, 1)
}

export function modelContextPercentage(context) {
  const estimated = context?.estimatedInputTokens
  const budget = context?.inputBudgetTokens
  if (!validAmount(estimated) || !validAmount(budget) || budget === 0) return null
  return Math.round(estimated / budget * 100)
}
