export function canonicalResearchSourceUrl(value) {
  if (typeof value !== 'string') return null
  try {
    const url = new URL(value)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null
    url.hash = ''
    return url.href
  } catch {
    return null
  }
}

export function mergeResearchSources(existing = [], incoming = []) {
  const sources = new Map()
  for (const source of [
    ...(Array.isArray(existing) ? existing : []),
    ...(Array.isArray(incoming) ? incoming : [])
  ]) {
    if (source?.citationEligible === false) continue
    const url = canonicalResearchSourceUrl(source?.url)
    if (!url) continue
    const title = typeof source.title === 'string' ? source.title.trim() : ''
    sources.set(url, { title: title || new URL(url).hostname, url })
  }
  return [...sources.values()]
}
