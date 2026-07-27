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

export function linkedResearchSources(research) {
  if (!Array.isArray(research?.linkedSources)) return []

  return research.linkedSources.flatMap((source) => {
    if (typeof source?.url !== 'string' || typeof source?.title !== 'string') return []
    const url = canonicalResearchSourceUrl(source.url)
    if (!url) return []
    return [{ title: source.title.trim() || new URL(url).hostname, url }]
  })
}
