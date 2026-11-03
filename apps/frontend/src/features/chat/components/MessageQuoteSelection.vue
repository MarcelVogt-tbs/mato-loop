<template>
  <div v-if="highlights.length" class="absolute-full overflow-hidden no-pointer-events">
    <div v-for="(rect, index) in highlights" :key="index" class="chat-quote-highlight absolute bg-primary" :style="rect" />
  </div>
  <Teleport to="body">
    <div v-if="text && !open" class="chat-quote-selection fixed" :style="position">
      <q-btn ref="button" dense no-caps unelevated size="sm" color="primary" icon="format_quote" label="Zitieren" type="button" class="chat-quote-selection__button" aria-label="Auswahl zitieren" @pointerdown.prevent @click.stop="openEditor" />
    </div>
    <template v-if="open && !useDialog">
      <div class="chat-quote-selection fixed" :style="editorPosition">
        <AppMenu ref="editorMenu" :model-value="open" no-parent-event no-refocus anchor="top left" self="top left" :offset="[0, 0]" :max-width="`${editorWidth}px`" :max-height="`min(45dvh, 360px, ${editorPosition.maxHeight})`" :style="{ opacity: editorSize.height ? undefined : 0 }" @hide="reset">
          <q-resize-observer :debounce="0" @resize="resizeEditor" />
          <QuoteEditor class="chat-quote-popover q-pa-sm" :quote="{ text, comment: '' }" :message-size="messageSize" @save="confirm" @cancel="reset" />
        </AppMenu>
      </div>
    </template>
    <q-dialog v-if="useDialog" :model-value="open" :transition-duration="0" @hide="reset">
      <q-card class="composer-quote-editor">
        <q-card-section class="text-subtitle1">Zitat kommentieren</q-card-section>
        <QuoteEditor v-if="open" class="q-px-md q-pb-md" :quote="{ text, comment: '' }" show-text :message-size="messageSize" @save="confirm" @cancel="reset" />
      </q-card>
    </q-dialog>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useQuasar } from 'quasar'
import AppMenu from 'src/shared/ui/AppMenu.vue'
import QuoteEditor from './QuoteEditor.vue'
import { quotePosition } from '../quote-position'

const props = defineProps({ target: { type: Object, default: null }, messageSize: { type: String, default: 'standard' } })
const emit = defineEmits(['quote'])
const $q = useQuasar()
const mobile = computed(() => $q.screen.lt.sm)
const text = ref('')
const bounds = ref(null)
const extent = ref(null)
const buttonSize = ref({ width: 0, height: 0 })
const editorSize = ref({ width: 0, height: 0 })
const position = computed(() => extent.value && quotePosition(bounds.value, extent.value, buttonSize.value))
const editorWidth = computed(() => Math.min(420, bounds.value.right - bounds.value.left - 16))
const editorPosition = computed(() => extent.value && quotePosition(bounds.value, extent.value, {
  width: editorSize.value.width || editorWidth.value,
  height: editorSize.value.height || buttonSize.value.height
}))
const highlights = ref([])
const range = shallowRef(null)
const open = ref(false)
const useDialog = ref(false)
const button = ref(null)
const editorMenu = ref(null)
let backward = false
let pointer = false
let timer
let frame

function reset() {
  clearTimeout(timer)
  open.value = false
  text.value = ''
  range.value = null
  extent.value = null
  editorSize.value = { width: 0, height: 0 }
  highlights.value = []
}

function measureSelection() {
  if (!range.value || range.value.collapsed || !props.target?.isConnected) return false
  const rects = []
  const walker = document.createTreeWalker(props.target, NodeFilter.SHOW_TEXT)
  while (walker.nextNode()) {
    const node = walker.currentNode
    if (!range.value.intersectsNode(node)) continue
    const part = document.createRange()
    part.selectNodeContents(node)
    if (node === range.value.startContainer) part.setStart(node, range.value.startOffset)
    if (node === range.value.endContainer) part.setEnd(node, range.value.endOffset)
    rects.push(...Array.from(part.getClientRects()).filter(rect => rect.width && rect.height))
  }
  if (!rects.length) return false
  const target = props.target.getBoundingClientRect()
  const view = props.target.closest('#chat-scroll-area')?.getBoundingClientRect()
  bounds.value = {
    top: Math.max(0, view?.top ?? 0),
    bottom: Math.min(window.innerHeight, view?.bottom ?? window.innerHeight),
    left: Math.max(0, view?.left ?? target.left),
    right: Math.min(window.innerWidth, view?.right ?? target.right)
  }
  const top = Math.min(...rects.map(rect => rect.top))
  const bottom = Math.max(...rects.map(rect => rect.bottom))
  if (!open.value && (bottom <= bounds.value.top || top >= bounds.value.bottom)) return false
  extent.value = {
    top, bottom,
    left: Math.min(...rects.map(rect => rect.left)),
    right: Math.max(...rects.map(rect => rect.right)),
    preferAbove: backward || bottom - top < Math.min(...rects.map(rect => rect.height)) * 3.5
  }
  if (!open.value) return true
  const parent = props.target.parentElement.getBoundingClientRect()
  highlights.value = [...new Map(rects.map(rect => [
    [rect.left, rect.top, rect.width, rect.height].join(','),
    { left: `${rect.left - parent.left}px`, top: `${rect.top - parent.top}px`, width: `${rect.width}px`, height: `${rect.height}px` }
  ])).values()]
  return true
}

async function updateSelection() {
  if (open.value || pointer) return
  const selection = window.getSelection()
  if (!selection?.rangeCount || selection.isCollapsed || !props.target) return reset()
  const selectedRange = selection.getRangeAt(0).cloneRange()
  if (!selectedRange.intersectsNode(props.target)) return reset()
  const original = selectedRange.toString().trim()
  const content = document.createRange()
  content.selectNodeContents(props.target)
  // Browsers may end a paragraph selection at offset zero of the next element.
  if (selectedRange.compareBoundaryPoints(Range.START_TO_START, content) < 0) selectedRange.setStart(content.startContainer, content.startOffset)
  if (selectedRange.compareBoundaryPoints(Range.END_TO_END, content) > 0) selectedRange.setEnd(content.endContainer, content.endOffset)
  if (selectedRange.collapsed || selectedRange.toString().trim() !== original) return reset()
  const selected = selection.toString().trim()
  if (!selected) return reset()
  const anchor = document.createRange()
  anchor.setStart(selection.anchorNode, selection.anchorOffset)
  backward = anchor.comparePoint(selection.focusNode, selection.focusOffset) < 0
  range.value = selectedRange
  if (!measureSelection()) return reset()
  text.value = selected
  await nextTick()
  if (!button.value || open.value) return
  buttonSize.value = button.value.$el.getBoundingClientRect()
}

function scheduleSelection() {
  if (open.value) return
  clearTimeout(timer)
  if (window.getSelection()?.isCollapsed) return reset()
  if (!pointer) timer = setTimeout(updateSelection, 150)
}

function pointerDown(event) {
  if (event.button !== 0 || button.value?.$el.contains(event.target)) return
  pointer = true
  clearTimeout(timer)
  if (!open.value) text.value = ''
}

function releasePointer() {
  if (!pointer) return
  pointer = false
  scheduleSelection()
}

function schedulePosition() {
  if (frame !== undefined || !range.value) return
  frame = requestAnimationFrame(() => {
    frame = undefined
    if (!measureSelection()) return reset()
    void nextTick(() => editorMenu.value?.updatePosition())
  })
}

function resizeEditor(size) {
  editorSize.value = size
  void nextTick(() => editorMenu.value?.updatePosition())
}

function openEditor() {
  useDialog.value = mobile.value
    || extent.value.bottom - extent.value.top >= bounds.value.bottom - bounds.value.top - 16
    || Number.parseFloat(editorPosition.value.maxHeight) < buttonSize.value.height
  open.value = true
  measureSelection()
  window.getSelection()?.removeAllRanges()
}
function confirm(quote) {
  emit('quote', quote)
  reset()
}

onMounted(() => {
  document.addEventListener('selectionchange', scheduleSelection)
  document.addEventListener('pointerdown', pointerDown)
  document.addEventListener('pointerup', releasePointer)
  document.addEventListener('pointercancel', releasePointer)
  document.addEventListener('scroll', schedulePosition, true)
  window.addEventListener('blur', releasePointer)
  window.addEventListener('resize', schedulePosition)
})
watch(() => props.target, (target, previous, onCleanup) => {
  if (!target || typeof ResizeObserver === 'undefined') return
  const observer = new ResizeObserver(schedulePosition)
  observer.observe(target)
  const view = target.closest('#chat-scroll-area')
  if (view) observer.observe(view)
  const content = target.closest('.chat-page__scroll-content')
  if (content) observer.observe(content)
  onCleanup(() => observer.disconnect())
}, { immediate: true, flush: 'post' })
onBeforeUnmount(() => {
  clearTimeout(timer)
  if (frame !== undefined) cancelAnimationFrame(frame)
  document.removeEventListener('selectionchange', scheduleSelection)
  document.removeEventListener('pointerdown', pointerDown)
  document.removeEventListener('pointerup', releasePointer)
  document.removeEventListener('pointercancel', releasePointer)
  document.removeEventListener('scroll', schedulePosition, true)
  window.removeEventListener('blur', releasePointer)
  window.removeEventListener('resize', schedulePosition)
})
</script>
