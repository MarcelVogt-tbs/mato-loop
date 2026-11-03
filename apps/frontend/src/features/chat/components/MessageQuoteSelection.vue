<template>
  <div v-if="highlights.length" class="absolute-full overflow-hidden no-pointer-events">
    <div v-for="(rect, index) in highlights" :key="index" class="chat-quote-highlight absolute bg-primary" :style="rect" />
  </div>
  <Teleport to="body">
    <div v-if="text && !open" class="chat-quote-selection fixed" :style="position">
      <q-btn ref="button" dense no-caps unelevated size="sm" color="primary" icon="format_quote" label="Zitieren" type="button" class="chat-quote-selection__button" aria-label="Auswahl zitieren" @pointerdown.prevent @click.stop="openEditor" />
    </div>
    <template v-if="open && !mobile">
      <div class="chat-quote-selection fixed" :style="editorPosition">
        <AppMenu :model-value="open" no-parent-event no-refocus anchor="bottom left" self="top left" :offset="[0, 4]" :max-width="editorWidth" max-height="min(45dvh, 360px)" @hide="reset">
          <QuoteEditor class="chat-quote-popover q-pa-sm" :quote="{ text, comment: '' }" :message-size="messageSize" @save="confirm" @cancel="reset" />
        </AppMenu>
      </div>
    </template>
    <q-dialog v-if="mobile" :model-value="open" :transition-duration="0" @hide="reset">
      <q-card class="composer-quote-editor">
        <q-card-section class="text-subtitle1">Zitat kommentieren</q-card-section>
        <QuoteEditor v-if="open" class="q-px-md q-pb-md" :quote="{ text, comment: '' }" show-text :message-size="messageSize" @save="confirm" @cancel="reset" />
      </q-card>
    </q-dialog>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { useQuasar } from 'quasar'
import AppMenu from 'src/shared/ui/AppMenu.vue'
import QuoteEditor from './QuoteEditor.vue'

const props = defineProps({ target: { type: Object, default: null }, messageSize: { type: String, default: 'standard' } })
const emit = defineEmits(['quote'])
const $q = useQuasar()
const mobile = computed(() => $q.screen.lt.sm)
const text = ref('')
const position = ref({})
const editorPosition = ref({})
const editorWidth = ref('calc(100vw - 32px)')
const highlights = ref([])
const range = shallowRef(null)
const open = ref(false)
const button = ref(null)

function reset() {
  open.value = false
  text.value = ''
  range.value = null
  highlights.value = []
}

function positionEditor() {
  if (!range.value || !props.target?.isConnected) return
  const rect = range.value.getBoundingClientRect()
  const bounds = props.target.getBoundingClientRect()
  const width = Math.min(420, bounds.width, window.innerWidth - 16)
  editorWidth.value = `${width}px`
  editorPosition.value = {
    left: `${Math.max(8, bounds.left, Math.min(rect.left, bounds.right - width, window.innerWidth - width - 8))}px`,
    top: `${rect.bottom}px`
  }
  const rects = Array.from(range.value.getClientRects()).filter(rect => rect.width && rect.height)
  const parent = props.target.parentElement.getBoundingClientRect()
  highlights.value = [...new Map(rects.map(rect => [
    [rect.left, rect.top, rect.width, rect.height].join(','),
    { left: `${rect.left - parent.left}px`, top: `${rect.top - parent.top}px`, width: `${rect.width}px`, height: `${rect.height}px` }
  ])).values()]
}

async function updateSelection() {
  if (open.value) return positionEditor()
  const selection = window.getSelection()
  if (!selection?.rangeCount || selection.isCollapsed
    || !props.target?.contains(selection.anchorNode) || !props.target.contains(selection.focusNode)) return reset()
  const selected = selection.toString().trim()
  if (!selected) return reset()
  range.value = selection.getRangeAt(0).cloneRange()
  const rect = range.value.getBoundingClientRect()
  const bounds = props.target.getBoundingClientRect()
  if (rect.bottom < 0 || rect.top > window.innerHeight) return reset()
  text.value = selected
  await nextTick()
  if (!button.value || open.value) return
  const size = button.value.$el.getBoundingClientRect()
  position.value = {
    left: `${Math.max(8, bounds.left, Math.min(rect.left, bounds.right - size.width - 8, window.innerWidth - size.width - 8))}px`,
    bottom: `${Math.max(8, Math.min(window.innerHeight - rect.top + 4, window.innerHeight - size.height - 8))}px`
  }
}

function openEditor() {
  positionEditor()
  open.value = true
  window.getSelection()?.removeAllRanges()
}
function confirm(quote) {
  emit('quote', quote)
  reset()
}

onMounted(() => {
  document.addEventListener('selectionchange', updateSelection)
  document.addEventListener('scroll', updateSelection, true)
  window.addEventListener('resize', updateSelection)
})
onBeforeUnmount(() => {
  document.removeEventListener('selectionchange', updateSelection)
  document.removeEventListener('scroll', updateSelection, true)
  window.removeEventListener('resize', updateSelection)
})
</script>
