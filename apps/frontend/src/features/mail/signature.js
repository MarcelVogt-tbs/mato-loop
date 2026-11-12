import createDOMPurify from 'dompurify'

const styles = new Set([
  'color', 'background-color', 'font-family', 'font-size', 'font-weight', 'font-style',
  'line-height', 'text-align', 'text-decoration', 'vertical-align', 'white-space',
  'width', 'height', 'min-width', 'max-width', 'border-collapse', 'border-spacing',
  ...['margin', 'padding', 'border'].flatMap(name => [name, ...['top', 'right', 'bottom', 'left'].map(side => `${name}-${side}`)]),
  'border-color', 'border-style', 'border-width'
])
const attributes = {
  a: ['href', 'title'],
  img: ['src', 'alt', 'width', 'height'],
  font: ['face', 'color', 'size'],
  table: ['width', 'height', 'border', 'cellpadding', 'cellspacing', 'align', 'bgcolor', 'role'],
  td: ['width', 'height', 'align', 'valign', 'colspan', 'rowspan', 'bgcolor'],
  th: ['width', 'height', 'align', 'valign', 'colspan', 'rowspan', 'bgcolor'],
  tr: ['align', 'valign', 'bgcolor']
}

function safeStyle(value) {
  if (value.length > 2000) return ''
  return value.split(';').flatMap(declaration => {
    const separator = declaration.indexOf(':')
    if (separator < 0) return []
    const name = declaration.slice(0, separator).trim().toLowerCase()
    const content = declaration.slice(separator + 1).replace(/\s*!important\s*$/i, '').trim()
    const plain = content.replace(/\b(?:rgba?|hsla?)\([0-9.,%+\s-]+\)/gi, '0')
    return styles.has(name) && /^[a-z0-9#.,%'"\s-]+$/i.test(plain) ? [`${name}:${content}`] : []
  }).join(';')
}

function validAttribute(tag, name, value) {
  if (name === 'src') return /^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(value)
  if (name === 'href') return /^(?:https?:\/\/|mailto:)/i.test(value)
  if (['width', 'height'].includes(name)) {
    return /^\d+$/.test(value) && Number(value) >= 1 && Number(value) <= 2048 ||
      tag !== 'img' && /^\d+%$/.test(value) && Number(value.slice(0, -1)) >= 1 && Number(value.slice(0, -1)) <= 100
  }
  if (['border', 'cellpadding', 'cellspacing', 'colspan', 'rowspan'].includes(name)) return /^\d{1,2}$/.test(value)
  if (name === 'align') return /^(?:left|center|right|justify)$/i.test(value)
  if (name === 'valign') return /^(?:top|middle|bottom|baseline)$/i.test(value)
  if (name === 'role') return value === 'presentation'
  if (name === 'face') return value.length <= 200 && /^[a-z0-9 ,'-]+$/i.test(value)
  if (name === 'size') return /^[+-]?[1-7]$/.test(value)
  if (['color', 'bgcolor'].includes(name)) return /^#[a-f0-9]{3,8}$/i.test(value) || /^[a-z]+$/i.test(value)
  return true
}

export function prepareSignature(value) {
  const purifier = createDOMPurify(window)
  let missingImages = 0
  purifier.addHook('uponSanitizeAttribute', (node, attribute) => {
    const tag = node.nodeName.toLowerCase()
    if (attribute.attrName === 'src' && /^data:image\/(?:png|jpeg|webp);base64,/.test(attribute.attrValue)) {
      const comma = attribute.attrValue.indexOf(',')
      attribute.attrValue = attribute.attrValue.slice(0, comma + 1) + attribute.attrValue.slice(comma + 1).replace(/[\t\n\f\r ]/g, '')
    }
    if (attribute.attrName === 'style') {
      attribute.attrValue = safeStyle(attribute.attrValue)
      attribute.keepAttr = Boolean(attribute.attrValue)
    } else {
      attribute.keepAttr = (attributes[tag] || []).includes(attribute.attrName) && validAttribute(tag, attribute.attrName, attribute.attrValue)
    }
  })
  purifier.addHook('afterSanitizeAttributes', node => {
    if (node.nodeName === 'IMG' && !node.hasAttribute('src')) {
      missingImages++
      node.replaceWith(document.createTextNode(node.getAttribute('alt') || ''))
    }
  })
  const html = purifier.sanitize(value || '', {
    ALLOWED_TAGS: ['a', 'b', 'br', 'div', 'em', 'font', 'i', 'img', 'li', 'ol', 'p', 's', 'span', 'strike', 'strong', 'u', 'ul',
      'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th'],
    ALLOWED_ATTR: ['style', ...new Set(Object.values(attributes).flat())],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false
  })
  return { html, missingImages }
}

export function safeSignature(value) {
  return prepareSignature(value).html
}
