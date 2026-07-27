<template>
  <div
    class="chat-markdown"
    @click="handleClick"
    @error.capture="handleImageError"
    v-html="rendered.html"
  />
</template>

<script setup>
import 'katex/dist/katex.min.css'

import { copyToClipboard, useQuasar } from 'quasar'
import { onBeforeUnmount, ref, watch } from 'vue'

import { renderMarkdown } from 'src/utils/markdown'
import { verifyExternalImage } from 'src/utils/image-verification'

defineOptions({ name: 'MessageContent' })

const props = defineProps({
  content: {
    type: String,
    default: ''
  },
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
const emit = defineEmits(['trust-image-origin'])
const $q = useQuasar()
const rendered = ref({ codeBlocks: [], html: '', externalImages: [] })
const imageStates = ref({})
const verificationTasks = new Map()
let renderFrame = null

function renderContent() {
  renderFrame = null
  rendered.value = renderMarkdown(props.content, {
    imageStates: imageStates.value,
    researchSources: props.researchSources,
    streaming: props.streaming,
    trustedImageOrigins: props.trustedImageOrigins
  })
  syncImageVerifications()
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
  () => [props.content, props.researchSources, props.streaming, props.trustedImageOrigins],
  scheduleRender,
  { immediate: true }
)

onBeforeUnmount(() => {
  if (renderFrame !== null) window.cancelAnimationFrame(renderFrame)
  for (const task of verificationTasks.values()) task.cancel()
  verificationTasks.clear()
})
</script>
