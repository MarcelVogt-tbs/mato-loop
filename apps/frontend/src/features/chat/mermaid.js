import DOMPurify from 'dompurify'

export const MAX_MERMAID_SOURCE_LENGTH = 20_000
export const MAX_MERMAID_CACHE_ENTRIES = 32

const LIGHT_THEME = {
  background: '#ffffff',
  primaryColor: '#dbeafe',
  primaryBorderColor: '#2563eb',
  primaryTextColor: '#1e3a8a',
  secondaryColor: '#ede9fe',
  secondaryBorderColor: '#7c3aed',
  secondaryTextColor: '#4c1d95',
  tertiaryColor: '#f1f5f9',
  tertiaryBorderColor: '#64748b',
  tertiaryTextColor: '#0f172a',
  lineColor: '#64748b',
  textColor: '#0f172a'
}

const DARK_THEME = {
  darkMode: true,
  background: '#191919',
  primaryColor: '#172554',
  primaryBorderColor: '#60a5fa',
  primaryTextColor: '#eff6ff',
  secondaryColor: '#2e1065',
  secondaryBorderColor: '#a78bfa',
  secondaryTextColor: '#f5f3ff',
  tertiaryColor: '#1e293b',
  tertiaryBorderColor: '#64748b',
  tertiaryTextColor: '#f8fafc',
  lineColor: '#94a3b8',
  textColor: '#f8fafc'
}

let mermaidPromise
let renderQueue = Promise.resolve()
let renderId = 0
const renderCache = new Map()

function mermaidError(code) {
  return Object.assign(new Error(code), { code })
}

export function mermaidErrorMessage(error) {
  if (error?.code === 'mermaid.empty') return 'Das Diagramm enthält keinen Mermaid-Quelltext.'
  if (error?.code === 'mermaid.tooLarge') {
    return 'Das Diagramm ist zu groß und muss in mehrere Diagramme aufgeteilt werden.'
  }
  if (error?.code === 'mermaid.configurationForbidden') {
    return 'Das Diagramm enthält nicht erlaubte Mermaid-Konfiguration.'
  }
  if (error?.code === 'mermaid.unavailable') {
    return 'Der Diagramm-Renderer konnte nicht geladen werden.'
  }
  return 'Das Diagramm ist syntaktisch ungültig oder zu komplex.'
}

function removeCssAtRule(css, rule) {
  let result = String(css || '')
  let position = result.toLowerCase().indexOf(rule)
  while (position !== -1) {
    const opening = result.indexOf('{', position + rule.length)
    if (opening === -1) return result.slice(0, position)
    let depth = 1
    let end = opening + 1
    while (end < result.length && depth) {
      if (result[end] === '{') depth += 1
      else if (result[end] === '}') depth -= 1
      end += 1
    }
    result = result.slice(0, position) + result.slice(end)
    position = result.toLowerCase().indexOf(rule, position)
  }
  return result
}

function inertCss(value) {
  return removeCssAtRule(removeCssAtRule(value, '@keyframes'), '@-webkit-keyframes')
    .replace(/(^|[;{])\s*(?:-webkit-)?(?:animation|transition)(?:-[\w-]+)?\s*:[^;}]*;?/gi, '$1')
}

export function validateMermaidSource(source) {
  const value = String(source || '')
  if (!value.trim()) throw mermaidError('mermaid.empty')
  if (value.length > MAX_MERMAID_SOURCE_LENGTH) throw mermaidError('mermaid.tooLarge')
  if (/^\s*---(?:\r?\n|$)/.test(value) || value.includes('%%{')) {
    throw mermaidError('mermaid.configurationForbidden')
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
      if (name === 'style') {
        const safeStyle = inertCss(content)
        if (safeStyle) element.setAttribute(attribute.name, safeStyle)
        else element.removeAttribute(attribute.name)
      }
      if ((name === 'href' || name === 'xlink:href') && !content.startsWith('#')) {
        element.removeAttribute(attribute.name)
      }
      if (/url\s*\(/i.test(content) && !/^url\(\s*['"]?#[A-Za-z0-9_.:-]+['"]?\s*\)$/i.test(content)) {
        element.removeAttribute(attribute.name)
      }
    }
  }
  for (const style of root.querySelectorAll('style')) {
    style.textContent = inertCss(style.textContent)
    if (/url\s*\(\s*(?!['"]?#)/i.test(style.textContent || '')) style.remove()
  }
  const viewBox = root.getAttribute('viewBox')?.trim().split(/[\s,]+/).map(Number)
  if (viewBox?.length === 4 && Number.isFinite(viewBox[2]) && viewBox[2] > 0) {
    root.setAttribute('width', String(viewBox[2]))
  }
  return root.outerHTML
}

async function render(source, dark) {
  const content = validateMermaidSource(source)
  let mermaid
  try {
    mermaidPromise ||= import('mermaid').then((module) => module.default)
    mermaid = await mermaidPromise
  } catch {
    throw mermaidError('mermaid.unavailable')
  }
  try {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      htmlLabels: false,
      logLevel: 'fatal',
      maxEdges: 300,
      maxTextSize: MAX_MERMAID_SOURCE_LENGTH,
      suppressErrorRendering: true,
      theme: 'base',
      themeVariables: dark ? DARK_THEME : LIGHT_THEME
    })
    const result = await mermaid.render(`matoloop-mermaid-${++renderId}`, content)
    return sanitizeMermaidSvg(result.svg)
  } catch {
    throw mermaidError('mermaid.invalid')
  }
}

export function renderMermaid(source, { dark = false } = {}) {
  const key = `${dark ? 'dark' : 'light'}\u0000${String(source || '')}`
  const cached = renderCache.get(key)
  if (cached) {
    renderCache.delete(key)
    renderCache.set(key, cached)
    return cached
  }
  const task = renderQueue.then(() => render(source, dark))
  renderQueue = task.catch(() => {})
  renderCache.set(key, task)
  if (renderCache.size > MAX_MERMAID_CACHE_ENTRIES) {
    renderCache.delete(renderCache.keys().next().value)
  }
  return task
}
