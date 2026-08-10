<template>
  <div
    ref="contentRoot"
    class="chat-markdown"
    @click="handleClick"
    @error.capture="handleImageError"
    v-html="rendered.html"
  />
</template>

<script setup>
import 'katex/dist/katex.min.css'

import { copyToClipboard, useQuasar } from 'quasar'
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

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
let renderFrame = null
let renderVersion = 0

function renderContent() {
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
    const source = blocks[Number(placeholder.dataset.markdownMermaid)]
    if (typeof source !== 'string') continue
    try {
      const svg = await renderMermaid(source, {
        dark: !props.forceLight && $q.dark.isActive
      })
      if (version !== renderVersion || !placeholder.isConnected) return
      placeholder.innerHTML = svg
      placeholder.removeAttribute('aria-live')
      placeholder.removeAttribute('role')
      const diagram = placeholder.querySelector('svg')
      if (diagram && !diagram.hasAttribute('aria-label') && !diagram.hasAttribute('aria-labelledby')) {
        diagram.setAttribute('aria-label', 'Mermaid-Diagramm')
      }
    } catch {
      if (version !== renderVersion || !placeholder.isConnected) return
      const error = document.createElement('div')
      error.className = 'chat-markdown__mermaid-error'
      error.textContent = 'Das Diagramm konnte nicht dargestellt werden.'
      placeholder.prepend(error)
      placeholder.setAttribute('role', 'alert')
    }
  }
  if (version === renderVersion) emit('rendered')
}

function scheduleRender() {
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
    $q.dark.isActive
  ],
  scheduleRender,
  { immediate: true }
)

onBeforeUnmount(() => {
  renderVersion += 1
  if (renderFrame !== null) window.cancelAnimationFrame(renderFrame)
  for (const task of verificationTasks.values()) task.cancel()
  verificationTasks.clear()
})
</script>
