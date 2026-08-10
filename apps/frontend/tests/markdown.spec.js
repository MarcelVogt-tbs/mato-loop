// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'

import { verifyExternalImage } from '../src/utils/image-verification'
import {
  classifyMarkdownImageUrl,
  containsMarkdownImage,
  renderMarkdown,
  safeMarkdownLinkUrl
} from '../src/utils/markdown'
import { markdownPrintRequest } from '../src/utils/markdown-print'
import { sanitizeMermaidSvg, validateMermaidSource } from '../src/utils/mermaid'

const internalImage = '/api/sessions/550e8400-e29b-41d4-a716-446655440000/messages/550e8400-e29b-41d4-a716-446655440001/attachments/550e8400-e29b-41d4-a716-446655440002'

function renderedDom(markdown, options) {
  const result = renderMarkdown(markdown, options)
  const root = document.createElement('div')
  root.innerHTML = result.html
  return { ...result, root }
}

describe('assistant markdown rendering', () => {
  it('detects active Markdown and HTML image syntax for safe editor mode selection', () => {
    expect(containsMarkdownImage('![Alt](https://example.com/image.png)')).toBe(true)
    expect(containsMarkdownImage('<img src="https://example.com/image.png">')).toBe(true)
    expect(containsMarkdownImage('\\![nur Text](https://example.com/image.png)')).toBe(false)
    expect(containsMarkdownImage('[normaler Link](https://example.com)')).toBe(false)
  })

  it('blocks all markdown images in static file previews', () => {
    const { root, externalImages } = renderedDom('![Extern](https://example.com/image.png)', {
      allowImages: false,
      interactive: false
    })

    expect(root.querySelector('img')).toBeNull()
    expect(root.textContent).toContain('[Bild: Extern]')
    expect(externalImages).toEqual([])
  })

  it('renders rich text, GFM tables, task lists and highlighted code', () => {
    const { root, codeBlocks, html } = renderedDom([
      '## Ergebnis',
      '',
      '**Fett** und *kursiv*',
      '',
      '| Name | Wert |',
      '| --- | --- |',
      '| Eins | 1 |',
      '',
      '- [x] erledigt',
      '',
      '```js',
      'const answer = 42',
      '```'
    ].join('\n'))

    expect(root.querySelector('h2')?.textContent).toBe('Ergebnis')
    expect(root.querySelector('strong')?.textContent).toBe('Fett')
    expect(root.querySelector('em')?.textContent).toBe('kursiv')
    expect(root.querySelectorAll('table tbody td')).toHaveLength(2)
    expect(root.querySelector('input[type="checkbox"]')?.disabled).toBe(true)
    expect(root.querySelector('pre code')?.classList.contains('hljs')).toBe(true)
    expect(root.querySelector('.hljs-keyword')?.textContent).toBe('const')
    expect(root.querySelector('[data-markdown-code]')?.getAttribute('aria-label')).toBe('Codeblock kopieren')
    expect(codeBlocks).toEqual(['const answer = 42\n'])
    expect(html).not.toContain('data-markdown-code="const answer')
  })

  it('renders safe HTML breaks without enabling raw HTML', () => {
    const { root } = renderedDom([
      'Eins<br>Zwei<br/>Drei<br />Vier',
      '',
      '`<br>`',
      '',
      '<br onclick="alert(1)">'
    ].join('\n'))

    expect(root.querySelectorAll('br')).toHaveLength(3)
    expect(root.querySelector('code')?.textContent).toBe('<br>')
    expect(root.textContent).toContain('<br onclick="alert(1)">')
    expect(root.querySelector('[onclick]')).toBeNull()
  })

  it('keeps multiple code block sources separate from rendered markup', () => {
    const { root, codeBlocks, html } = renderedDom('```txt\nerster Block\n```\n\n```\nzweiter Block\n```')

    expect([...root.querySelectorAll('[data-markdown-code]')].map((button) => button.dataset.markdownCode)).toEqual(['0', '1'])
    expect(codeBlocks).toEqual(['erster Block\n', 'zweiter Block\n'])
    expect(html).not.toContain('erster Block"')
    expect(html).not.toContain('zweiter Block"')
  })

  it('collects completed Mermaid fences without exposing their source in attributes', () => {
    const source = 'flowchart LR\n  A["<script>alert(1)</script>"] --> B\n'
    const { root, codeBlocks, html, mermaidBlocks } = renderedDom(`\`\`\`mermaid\n${source}\`\`\``)

    expect(root.querySelector('[data-markdown-mermaid]')?.dataset.markdownMermaid).toBe('0')
    expect(root.querySelector('[data-markdown-code]')?.getAttribute('aria-label')).toBe('Mermaid-Quelltext kopieren')
    expect(root.querySelector('script')).toBeNull()
    expect(codeBlocks).toEqual([source])
    expect(mermaidBlocks).toEqual([source])
    expect(html).not.toContain('data-markdown-mermaid="flowchart')
    expect(html).not.toContain('data-markdown-code="flowchart')
  })

  it('keeps Mermaid as code while streaming and renders it in static previews after completion', () => {
    const source = 'flowchart LR\n  A --> B\n'
    const streaming = renderedDom(`\`\`\`mermaid\n${source}\`\`\``, { streaming: true })
    expect(streaming.root.querySelector('[data-markdown-mermaid]')).toBeNull()
    expect(streaming.root.querySelector('pre')?.textContent).toContain('A --> B')
    expect(streaming.mermaidBlocks).toEqual([])

    const preview = renderedDom(`\`\`\`mermaid\n${source}\`\`\``, { interactive: false })
    expect(preview.root.querySelector('[data-markdown-mermaid]')).not.toBeNull()
    expect(preview.root.querySelector('[data-markdown-code]')).toBeNull()
    expect(preview.mermaidBlocks).toEqual([source])

    const similar = renderedDom(`\`\`\`mermaidish\n${source}\`\`\``)
    expect(similar.root.querySelector('[data-markdown-mermaid]')).toBeNull()
  })

  it('renders inline TeX with dollar and bracket delimiters', () => {
    const { root } = renderedDom('Weiter $\\rightarrow$ und \\(x^2\\).')

    expect(root.querySelectorAll('.katex')).toHaveLength(2)
    expect(root.querySelector('mo')?.textContent).toBe('→')
    expect(root.querySelector('msup')).not.toBeNull()
  })

  it('renders block fractions and matrices with accessible MathML', () => {
    const { root } = renderedDom([
      '$$',
      '\\frac{a}{b}',
      '$$',
      '',
      '\\[',
      '\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}',
      '\\]',
      '',
      '$\\xrightarrow{test}$'
    ].join('\n'))

    expect(root.querySelectorAll('.katex-display')).toHaveLength(2)
    expect(root.querySelector('mfrac')).not.toBeNull()
    expect(root.querySelector('mtable')).not.toBeNull()
    expect(root.querySelector('svg path')).not.toBeNull()
    expect(root.querySelector('annotation')?.getAttribute('encoding')).toBe('application/x-tex')
    expect([...root.querySelectorAll('[style]')].every((element) => element.closest('.katex'))).toBe(true)
  })

  it('keeps untrusted TeX commands and non-KaTeX styles inert', () => {
    const { root } = renderedDom([
      '$\\href{javascript:alert(1)}{x}$',
      '$\\includegraphics{https://example.com/x.png}$',
      '$\\htmlStyle{color:red}{x}$',
      '<span style="position:fixed">Fremdes HTML</span>'
    ].join('\n\n'))

    expect(root.querySelector('.katex')).not.toBeNull()
    expect(root.querySelector('.chat-markdown__math-error')).not.toBeNull()
    expect(root.querySelector('a[href^="javascript:"]')).toBeNull()
    expect(root.querySelector('.katex img')).toBeNull()
    expect(root.querySelector('[onload], [onerror]')).toBeNull()
    expect([...root.querySelectorAll('[style]')].every((element) => element.closest('.katex'))).toBe(true)
  })

  it('does not parse currency, escaped dollars or code as TeX', () => {
    const { root } = renderedDom('Preis: $5 bis $10. Literal \\$x\\$ und Code `$\\rightarrow$`.')

    expect(root.querySelector('.katex')).toBeNull()
    expect(root.querySelector('code')?.textContent).toBe('$\\rightarrow$')

    const formula = renderedDom('Berechnung: $5 + x$.').root
    expect(formula.querySelector('.katex')).not.toBeNull()
  })

  it('fails safely for incomplete streaming TeX', () => {
    for (const prefix of ['$', '$\\right', '$\\rightarrow', '$$\\frac{a}{']) {
      const { root } = renderedDom(prefix, { streaming: true })
      expect(root.querySelector('script')).toBeNull()
    }

    const { root } = renderedDom('$\\rightarrow$', { streaming: true })
    expect(root.querySelector('mo')?.textContent).toBe('→')
  })

  it('keeps raw HTML and dangerous URLs inert', () => {
    const { root } = renderedDom([
      '<script>alert(1)</script>',
      '<img src="x" onerror="alert(2)">',
      '[gefährlich](javascript:alert(3))',
      '![Pixel](data:image/svg+xml,<svg onload=alert(4)>)'
    ].join('\n\n'))

    expect(root.querySelector('script')).toBeNull()
    expect(root.querySelector('[onerror], [onload]')).toBeNull()
    expect(root.querySelector('img')).toBeNull()
    expect(root.querySelector('a[href^="javascript:"]')).toBeNull()
  })

  it('blocks external images without placing their URL in active markup', () => {
    const { root, html, externalImages } = renderedDom(
      '![Tracking-Pixel](https://images.example/pixel.png?user=secret)'
    )

    expect(root.querySelector('img')).toBeNull()
    expect(root.querySelector('[data-markdown-image]')?.textContent).toContain('images.example')
    expect(html).not.toContain('pixel.png')
    expect(externalImages).toEqual([expect.objectContaining({
      origin: 'https://images.example',
      url: 'https://images.example/pixel.png?user=secret'
    })])
  })

  it('checks trusted external images before rendering them', () => {
    const checking = renderedDom(
      '![Diagramm](https://images.example/diagram.png)',
      { trustedImageOrigins: ['https://images.example'] }
    )
    expect(checking.root.querySelector('img')).toBeNull()
    expect(checking.root.textContent).toContain('wird geprüft')

    const { root } = renderedDom(
      '![Diagramm](https://images.example/diagram.png)',
      {
        imageStates: { 'https://images.example/diagram.png': 'ready' },
        trustedImageOrigins: ['https://images.example']
      }
    )
    const image = root.querySelector('img')

    expect(image?.src).toBe('https://images.example/diagram.png')
    expect(image?.getAttribute('referrerpolicy')).toBe('no-referrer')
    expect(root.querySelector('button[data-markdown-image]')).toBeNull()
  })

  it('shows a retry action for failed external images', () => {
    const { root } = renderedDom(
      '![Fehlt](https://images.example/missing.png)',
      {
        imageStates: { 'https://images.example/missing.png': 'failed' },
        trustedImageOrigins: ['https://images.example']
      }
    )

    expect(root.querySelector('img')).toBeNull()
    expect(root.textContent).toContain('konnte nicht geladen oder decodiert werden')
    expect(root.querySelector('[data-markdown-image]')?.textContent).toBe('Erneut versuchen')
  })

  it('loads only exact internal attachment routes automatically', () => {
    const { root } = renderedDom(`![Intern](${internalImage})`)

    expect(root.querySelector('img')?.getAttribute('src')).toBe(internalImage)
    expect(classifyMarkdownImageUrl(internalImage).kind).toBe('internal')
    expect(classifyMarkdownImageUrl('/api/sessions/../secrets').kind).toBe('invalid')
  })

  it('restricts links and handles incomplete streaming markdown', () => {
    const downloadUrl = '/api/workspace/files/download?path=files%2Freport.pdf&scope=session&session_id=550e8400-e29b-41d4-a716-446655440000&root_identity=550e8400-e29b-41d4-a716-446655440000'
    expect(safeMarkdownLinkUrl('https://example.com/path')).toBe('https://example.com/path')
    expect(safeMarkdownLinkUrl('mailto:test@example.com')).toBe('mailto:test@example.com')
    expect(safeMarkdownLinkUrl(downloadUrl)).toBe(downloadUrl)
    expect(safeMarkdownLinkUrl('javascript:alert(1)')).toBeNull()
    expect(safeMarkdownLinkUrl('/relative/path')).toBeNull()

    const links = renderedDom(`[Extern](https://example.com) [Download](${downloadUrl}) [Relativ](/relative/path)`).root
    const externalLink = links.querySelector('a[href="https://example.com/"]')
    const downloadLink = links.querySelector(`a[href="${downloadUrl}"]`)
    const invalidLink = links.querySelector('.chat-markdown__link--invalid')
    expect(externalLink?.getAttribute('target')).toBe('_blank')
    expect(externalLink?.getAttribute('rel')).toBe('noopener noreferrer')
    expect(downloadLink).not.toBeNull()
    expect(downloadLink?.hasAttribute('target')).toBe(false)
    expect(invalidLink?.hasAttribute('href')).toBe(false)

    const { root } = renderedDom('```js\nconst streaming = true')
    expect(root.querySelector('pre code')?.textContent).toContain('const streaming = true')
  })

  it('renders only allowlisted research URLs as inline source links', () => {
    const { root } = renderedDom(
      'Aussage [Primärquelle](https://example.com/report#section) und [normaler Link](https://other.example).',
      {
        researchSources: [
          { title: 'Offizieller Bericht', url: 'https://example.com/report' }
        ]
      }
    )

    const source = root.querySelector('a[href="https://example.com/report#section"]')
    const regular = root.querySelector('a[href="https://other.example/"]')
    expect(source?.classList.contains('chat-markdown__source-link')).toBe(true)
    expect(source?.getAttribute('title')).toBe('Offizieller Bericht')
    expect(source?.getAttribute('target')).toBe('_blank')
    expect(regular?.classList.contains('chat-markdown__source-link')).toBe(false)

    const nextRender = renderedDom('[Primärquelle](https://example.com/report)').root
    expect(nextRender.querySelector('.chat-markdown__source-link')).toBeNull()
  })
})

describe('Mermaid safety', () => {
  it('rejects oversized and content-controlled Mermaid configuration', () => {
    expect(() => validateMermaidSource('%%{init: {"securityLevel": "loose"}}%%\ngraph LR\nA-->B')).toThrow()
    expect(() => validateMermaidSource('---\nconfig:\n  securityLevel: loose\n---\ngraph LR\nA-->B')).toThrow()
    expect(validateMermaidSource('graph LR\nA-->B')).toBe('graph LR\nA-->B')
  })

  it('sanitizes active and external SVG content while preserving local markers', () => {
    const svg = sanitizeMermaidSvg(`
      <svg xmlns="http://www.w3.org/2000/svg" id="diagram" viewBox="0 0 10 10" onload="alert(0)">
        <style>#diagram path { stroke: black; }</style>
        <marker id="arrow"><path d="M0 0L1 1" /></marker>
        <script>alert(1)</script>
        <foreignObject><div>HTML</div></foreignObject>
        <image href="https://tracking.example/pixel.png" />
        <a href="javascript:alert(2)"><text>Link</text></a>
        <path marker-end="url(#arrow)" onclick="alert(3)" d="M0 0L10 10" />
      </svg>
    `)
    const root = document.createElement('div')
    root.innerHTML = svg

    expect(root.querySelector('script, foreignObject, image, a, [onclick], [onload]')).toBeNull()
    expect(root.querySelector('svg')?.id).toBe('diagram')
    expect(root.querySelector('style')?.textContent).toContain('#diagram path')
    expect(root.querySelector('marker')?.id).toBe('arrow')
    expect(root.querySelector('path[marker-end]')?.getAttribute('marker-end')).toBe('url(#arrow)')
  })
})

describe('Markdown print requests', () => {
  it('keeps only the authenticated file context and expected revision', () => {
    const revision = 'a'.repeat(64)
    expect(markdownPrintRequest({
      content: '# must not travel',
      path: 'files/report.md',
      revision,
      root_identity: 'root-a',
      scope: 'session',
      session_id: 'session-a'
    })).toEqual({
      expectedRevision: revision,
      params: {
        path: 'files/report.md',
        root_identity: 'root-a',
        scope: 'session',
        session_id: 'session-a'
      }
    })
    expect(markdownPrintRequest({ path: 'report.md', scope: 'invalid' })).toBeNull()
  })
})

function fakeImage(decode = () => Promise.resolve()) {
  return {
    decode,
    onerror: null,
    onload: null,
    removeAttribute: vi.fn(),
    set src(value) {
      this.currentSrc = value
    }
  }
}

describe('external image verification', () => {
  it('requires a successful load and decode', async () => {
    const image = fakeImage()
    const cancelTimeout = vi.fn()
    const verification = verifyExternalImage('https://images.example/ok.png', {
      createImage: () => image,
      scheduleTimeout: () => 1,
      cancelTimeout
    })

    expect(image.currentSrc).toBe('https://images.example/ok.png')
    expect(image.referrerPolicy).toBe('no-referrer')
    await image.onload()
    await expect(verification.promise).resolves.toEqual({ status: 'ready', reason: null })
    expect(cancelTimeout).toHaveBeenCalledWith(1)
  })

  it('reports load and decode failures', async () => {
    const loadImage = fakeImage()
    const loadVerification = verifyExternalImage('https://images.example/missing.png', {
      createImage: () => loadImage,
      scheduleTimeout: () => 1,
      cancelTimeout: () => {}
    })
    loadImage.onerror()
    await expect(loadVerification.promise).resolves.toEqual({ status: 'failed', reason: 'load' })

    const decodeImage = fakeImage(() => Promise.reject(new Error('invalid image')))
    const decodeVerification = verifyExternalImage('https://images.example/broken.png', {
      createImage: () => decodeImage,
      scheduleTimeout: () => 2,
      cancelTimeout: () => {}
    })
    await decodeImage.onload()
    await expect(decodeVerification.promise).resolves.toEqual({ status: 'failed', reason: 'decode' })
  })

  it('fails closed on timeout and supports cancellation', async () => {
    let timeoutHandler
    const timeoutImage = fakeImage()
    const timeoutVerification = verifyExternalImage('https://images.example/slow.png', {
      createImage: () => timeoutImage,
      scheduleTimeout: (handler) => {
        timeoutHandler = handler
        return 3
      },
      cancelTimeout: () => {}
    })
    timeoutHandler()
    await expect(timeoutVerification.promise).resolves.toEqual({ status: 'failed', reason: 'timeout' })
    expect(timeoutImage.removeAttribute).toHaveBeenCalledWith('src')

    const cancelledImage = fakeImage()
    const cancelledVerification = verifyExternalImage('https://images.example/cancel.png', {
      createImage: () => cancelledImage,
      scheduleTimeout: () => 4,
      cancelTimeout: () => {}
    })
    cancelledVerification.cancel()
    await expect(cancelledVerification.promise).resolves.toEqual({ status: 'cancelled', reason: null })
    expect(cancelledImage.removeAttribute).toHaveBeenCalledWith('src')
  })
})
