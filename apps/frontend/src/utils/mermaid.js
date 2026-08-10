import DOMPurify from 'dompurify'

export const MAX_MERMAID_SOURCE_LENGTH = 20_000

let mermaidPromise
let renderQueue = Promise.resolve()
let renderId = 0

export function validateMermaidSource(source) {
  const value = String(source || '')
  if (!value.trim()) throw new Error('Mermaid source is empty')
  if (value.length > MAX_MERMAID_SOURCE_LENGTH) throw new Error('Mermaid source is too large')
  if (/^\s*---(?:\r?\n|$)/.test(value) || value.includes('%%{')) {
    throw new Error('Mermaid configuration directives are not allowed')
  }
  return value
}

export function sanitizeMermaidSvg(value) {
  const sanitized = DOMPurify.sanitize(String(value || ''), {
    ADD_TAGS: ['style'],
    FORBID_TAGS: ['a', 'foreignObject', 'iframe', 'image', 'script'],
    RETURN_TRUSTED_TYPE: false,
    USE_PROFILES: { svg: true, svgFilters: true }
  })
  const document = new DOMParser().parseFromString(sanitized, 'image/svg+xml')
  const root = document.documentElement
  if (root.localName !== 'svg' || document.querySelector('parsererror')) {
    throw new Error('Mermaid returned invalid SVG')
  }

  for (const element of [root, ...root.querySelectorAll('*')]) {
    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase()
      const content = attribute.value.trim()
      if (name.startsWith('on')) element.removeAttribute(attribute.name)
      if ((name === 'href' || name === 'xlink:href') && !content.startsWith('#')) {
        element.removeAttribute(attribute.name)
      }
      if (/url\s*\(/i.test(content) && !/^url\(\s*['"]?#[A-Za-z0-9_.:-]+['"]?\s*\)$/i.test(content)) {
        element.removeAttribute(attribute.name)
      }
    }
  }
  for (const style of root.querySelectorAll('style')) {
    if (/url\s*\(\s*(?!['"]?#)/i.test(style.textContent || '')) style.remove()
  }
  return root.outerHTML
}

async function render(source, dark) {
  const content = validateMermaidSource(source)
  mermaidPromise ||= import('mermaid').then((module) => module.default)
  const mermaid = await mermaidPromise
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: 'strict',
    htmlLabels: false,
    logLevel: 'fatal',
    maxEdges: 300,
    maxTextSize: MAX_MERMAID_SOURCE_LENGTH,
    suppressErrorRendering: true,
    theme: dark ? 'dark' : 'neutral'
  })
  const result = await mermaid.render(`matoloop-mermaid-${++renderId}`, content)
  return sanitizeMermaidSvg(result.svg)
}

export function renderMermaid(source, { dark = false } = {}) {
  const task = renderQueue.then(() => render(source, dark))
  renderQueue = task.catch(() => {})
  return task
}
