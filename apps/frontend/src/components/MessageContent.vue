<template>
  <div class="markdown-viewer">
    <div v-if="showViewerControls" class="markdown-viewer__actions">
      <q-tabs
        :model-value="viewerMode"
        no-caps
        dense
        shrink
        active-color="primary"
        indicator-color="transparent"
        active-class="markdown-viewer__mode-tab--active"
        aria-label="Markdown-Ansicht"
        @update:model-value="setViewerMode"
      >
        <q-tab class="markdown-viewer__mode-tab" name="preview" icon="visibility" aria-label="Vorschau" :ripple="false">
          <q-tooltip>Vorschau</q-tooltip>
        </q-tab>
        <q-tab class="markdown-viewer__mode-tab" name="source" icon="code" aria-label="Quelltext" :ripple="false">
          <q-tooltip>Quelltext</q-tooltip>
        </q-tab>
      </q-tabs>
      <q-btn
        class="markdown-viewer__copy"
        flat
        dense
        icon="content_copy"
        aria-label="Markdown kopieren"
        @click="copySource"
      >
        <q-tooltip>Markdown kopieren</q-tooltip>
      </q-btn>
    </div>
    <div
      v-if="viewerMode === 'preview'"
      ref="contentRoot"
      class="chat-markdown"
      @click="handleClick"
      @error.capture="handleImageError"
      v-html="rendered.html"
    />
    <div v-else class="chat-markdown">
      <div class="chat-markdown__code-block">
        <pre><code>{{ content }}</code></pre>
      </div>
    </div>

    <q-dialog v-model="mermaidDialog" maximized transition-show="fade" transition-hide="fade" @hide="clearMermaidImage">
      <q-card v-if="mermaidImage" class="app-file-dialog">
        <ImageViewerPanel
          :image="mermaidImage"
          fullscreen
          @close="mermaidDialog = false"
          @toggle-fullscreen="mermaidDialog = false"
        />
      </q-card>
    </q-dialog>
  </div>
</template>

<script setup>
import 'katex/dist/katex.min.css'

import { copyToClipboard, useQuasar } from 'quasar'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import ImageViewerPanel from 'components/ImageViewerPanel.vue'
import { renderMarkdown } from 'src/utils/markdown'
import { verifyExternalImage } from 'src/utils/image-verification'
import { renderMermaid } from 'src/utils/mermaid'

defineOptions({ name: 'MessageContent' })

const props = defineProps({
  content: {
    type: String,
    default: ''
  },
  allowImages: {
    type: Boolean,
    default: true
  },
  interactive: {
    type: Boolean,
    default: true
  },
  viewerControls: {
    type: Boolean,
    default: undefined
  },
  forceLight: Boolean,
  streaming: Boolean,
  researchSources: {
    type: Array,
    default: () => []
  },
  trustedImageOrigins: {
    type: Array,
    default: () => []
  }
})
const emit = defineEmits(['rendered', 'trust-image-origin'])
const $q = useQuasar()
const contentRoot = ref(null)
const rendered = ref({ codeBlocks: [], html: '', externalImages: [], mermaidBlocks: [] })
const imageStates = ref({})
const verificationTasks = new Map()
const viewerMode = ref('preview')
const mermaidDialog = ref(false)
const mermaidImage = ref(null)
const showViewerControls = computed(() => props.viewerControls ?? props.interactive)
let renderFrame = null
let renderVersion = 0
let mermaidObjectUrl = ''

function renderContent() {
  if (mermaidDialog.value) mermaidDialog.value = false
  renderFrame = null
  const version = ++renderVersion
  rendered.value = renderMarkdown(props.content, {
    allowImages: props.allowImages,
    imageStates: imageStates.value,
    interactive: props.interactive,
    researchSources: props.researchSources,
    streaming: props.streaming,
    trustedImageOrigins: props.trustedImageOrigins
  })
  syncImageVerifications()
  hydrateMermaid(version)
}

async function hydrateMermaid(version) {
  await nextTick()
  if (version !== renderVersion || !contentRoot.value) return
  const blocks = rendered.value.mermaidBlocks
  for (const placeholder of contentRoot.value.querySelectorAll('[data-markdown-mermaid]')) {
    const blockIndex = Number(placeholder.dataset.markdownMermaid)
    const source = blocks[blockIndex]
    if (typeof source !== 'string') continue
    const dark = !props.forceLight && $q.dark.isActive
    try {
      const svg = await renderMermaid(source, { dark })
      if (version !== renderVersion || !placeholder.isConnected) return
      placeholder.innerHTML = svg
      placeholder.removeAttribute('aria-live')
      placeholder.removeAttribute('role')
      const diagram = placeholder.querySelector('svg')
      if (diagram && !diagram.hasAttribute('aria-label') && !diagram.hasAttribute('aria-labelledby')) {
        diagram.setAttribute('aria-label', 'Mermaid-Diagramm')
      }
      if (diagram && showViewerControls.value && !props.streaming) {
        const button = document.createElement('button')
        button.type = 'button'
        button.className = 'chat-markdown__mermaid-fullscreen material-icons'
        button.setAttribute('aria-label', 'Diagramm im Vollbild öffnen')
        button.title = 'Im Vollbild öffnen'
        button.textContent = 'fullscreen'
        placeholder.appendChild(button)
      }
    } catch {
      if (version !== renderVersion || !placeholder.isConnected) return
      const error = document.createElement('div')
      error.className = 'chat-markdown__mermaid-error'
      error.textContent = 'Das Diagramm konnte nicht dargestellt werden.'
      placeholder.replaceChildren(error)
      placeholder.removeAttribute('aria-live')
      placeholder.setAttribute('role', 'alert')
    }
  }
  if (version === renderVersion) emit('rendered')
}

function setViewerMode(value) {
  if (!['preview', 'source'].includes(value) || value === viewerMode.value) return
  viewerMode.value = value
}

function scheduleRender() {
  if (viewerMode.value !== 'preview') {
    renderVersion += 1
    if (renderFrame !== null) window.cancelAnimationFrame(renderFrame)
    renderFrame = null
    return
  }
  if (!props.streaming) {
    if (renderFrame !== null) window.cancelAnimationFrame(renderFrame)
    renderContent()
    return
  }
  if (renderFrame === null) renderFrame = window.requestAnimationFrame(renderContent)
}

function setImageState(url, status) {
  imageStates.value = { ...imageStates.value, [url]: status }
  scheduleRender()
}

function verifyImage(image) {
  if (props.streaming || !image?.url || verificationTasks.has(image.url)) return
  setImageState(image.url, 'checking')
  const task = verifyExternalImage(image.url)
  verificationTasks.set(image.url, task)
  task.promise.then((result) => {
    if (verificationTasks.get(image.url) !== task) return
    verificationTasks.delete(image.url)
    if (result.status === 'cancelled') return
    setImageState(image.url, result.status)
  })
}

function syncImageVerifications() {
  const currentUrls = new Set(rendered.value.externalImages.map((image) => image.url))
  let nextStates = imageStates.value
  for (const [url, task] of verificationTasks) {
    if (currentUrls.has(url)) continue
    task.cancel()
    verificationTasks.delete(url)
    if (Object.hasOwn(nextStates, url)) {
      if (nextStates === imageStates.value) nextStates = { ...imageStates.value }
      delete nextStates[url]
    }
  }
  if (nextStates !== imageStates.value) imageStates.value = nextStates
  if (props.streaming) return
  for (const image of rendered.value.externalImages) {
    const trusted = props.trustedImageOrigins.includes(image.origin)
    if (trusted && !imageStates.value[image.url]) verifyImage(image)
  }
}

async function handleClick(event) {
  const mermaidButton = event.target.closest?.('.chat-markdown__mermaid-fullscreen')
  if (mermaidButton && event.currentTarget.contains(mermaidButton)) {
    const diagram = mermaidButton.closest('[data-markdown-mermaid]')?.querySelector('svg')
    if (diagram) openMermaidImage(diagram.outerHTML)
    return
  }

  const codeButton = event.target.closest?.('[data-markdown-code]')
  if (codeButton && event.currentTarget.contains(codeButton)) {
    const code = rendered.value.codeBlocks[Number(codeButton.dataset.markdownCode)]
    if (typeof code !== 'string') return
    try {
      await copyToClipboard(code)
      $q.notify({ type: 'positive', message: 'Code kopiert' })
    } catch {
      $q.notify({ type: 'negative', message: 'Code konnte nicht kopiert werden' })
    }
    return
  }

  const button = event.target.closest?.('[data-markdown-image]')
  if (!button || !event.currentTarget.contains(button)) return
  const image = rendered.value.externalImages[Number(button.dataset.markdownImage)]
  if (!image?.origin) return
  if (!props.trustedImageOrigins.includes(image.origin)) emit('trust-image-origin', image.origin)
  verifyImage(image)
}

function openMermaidImage(svg) {
  clearMermaidImage()
  mermaidObjectUrl = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }))
  mermaidImage.value = { url: mermaidObjectUrl, name: 'Mermaid-Diagramm.svg' }
  mermaidDialog.value = true
}

function clearMermaidImage() {
  if (mermaidObjectUrl) URL.revokeObjectURL(mermaidObjectUrl)
  mermaidObjectUrl = ''
  mermaidImage.value = null
}

async function copySource() {
  try {
    await copyToClipboard(props.content)
    $q.notify({ type: 'positive', message: 'Quelltext kopiert' })
  } catch {
    $q.notify({ type: 'negative', message: 'Quelltext konnte nicht kopiert werden' })
  }
}

function handleImageError(event) {
  const target = event.target
  if (!target.matches?.('img[data-markdown-image]')) return
  const image = rendered.value.externalImages[Number(target.dataset.markdownImage)]
  if (image?.url) setImageState(image.url, 'failed')
}

watch(
  () => [
    props.allowImages,
    props.content,
    props.forceLight,
    props.interactive,
    props.researchSources,
    props.streaming,
    props.trustedImageOrigins,
    $q.dark.isActive,
    viewerMode.value
  ],
  scheduleRender,
  { immediate: true }
)

onBeforeUnmount(() => {
  renderVersion += 1
  if (renderFrame !== null) window.cancelAnimationFrame(renderFrame)
  clearMermaidImage()
  for (const task of verificationTasks.values()) task.cancel()
  verificationTasks.clear()
})
</script>
