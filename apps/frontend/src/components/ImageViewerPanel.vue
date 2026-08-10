<template>
  <section
    ref="viewerRoot"
    class="file-viewer column no-wrap full-height"
    tabindex="-1"
    aria-label="Bildansicht"
    @keydown.esc="close"
  >
    <q-toolbar class="file-viewer__toolbar q-px-md">
      <q-toolbar-title class="text-subtitle1 text-weight-bold ellipsis">{{ image.name || 'Bild' }}</q-toolbar-title>
      <q-btn
        class="file-icon-action"
        flat
        round
        dense
        icon="open_in_new"
        :href="image.url"
        target="_blank"
        rel="noopener noreferrer"
        aria-label="Bild in neuem Tab öffnen"
      >
        <q-tooltip>In neuem Tab öffnen</q-tooltip>
      </q-btn>
      <q-btn class="file-icon-action" flat round dense icon="download" aria-label="Bild herunterladen" @click="download">
        <q-tooltip>Herunterladen</q-tooltip>
      </q-btn>
      <q-btn
        v-if="fullscreenToggle"
        class="file-icon-action"
        flat
        round
        dense
        :icon="fullscreen ? 'fullscreen_exit' : 'fullscreen'"
        :aria-label="fullscreen ? 'Vollbild verlassen' : 'Im Vollbild öffnen'"
        @click="emit('toggle-fullscreen')"
      >
        <q-tooltip>{{ fullscreen ? 'Vollbild verlassen' : 'Im Vollbild öffnen' }}</q-tooltip>
      </q-btn>
      <q-btn class="file-icon-action" flat round dense icon="close" aria-label="Bildansicht schließen" @click="emit('close')">
        <q-tooltip>Bildansicht schließen</q-tooltip>
      </q-btn>
    </q-toolbar>
    <q-separator />
    <div class="col file-viewer__content q-pa-md">
      <div v-if="loading" class="fit flex flex-center"><q-spinner color="primary" size="36px" /></div>
      <div v-else-if="failed" class="fit column flex-center text-center q-pa-xl" role="alert">
        <q-icon name="broken_image" size="64px" class="app-muted q-mb-md" />
        <div class="text-subtitle1">Bild konnte nicht dargestellt werden</div>
      </div>
      <img
        v-show="!loading && !failed"
        :src="image.url"
        :alt="image.name || 'Bildvorschau'"
        class="file-viewer__image"
        @load="loading = false"
        @error="imageFailed"
      />
    </div>
  </section>
</template>

<script setup>
import { nextTick, ref, watch } from 'vue'

defineOptions({ name: 'ImageViewerPanel' })

const props = defineProps({
  image: { type: Object, required: true },
  fullscreen: Boolean,
  fullscreenToggle: Boolean
})
const emit = defineEmits(['close', 'toggle-fullscreen'])
const viewerRoot = ref(null)
const loading = ref(true)
const failed = ref(false)

function close() {
  if (props.fullscreen) emit('toggle-fullscreen')
  else emit('close')
}

function download() {
  const anchor = document.createElement('a')
  anchor.href = props.image.url
  anchor.download = props.image.name || 'bild'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

function imageFailed() {
  loading.value = false
  failed.value = true
}

watch(
  () => props.image.url,
  async () => {
    loading.value = true
    failed.value = false
    await nextTick()
    viewerRoot.value?.focus()
  },
  { immediate: true }
)
</script>
