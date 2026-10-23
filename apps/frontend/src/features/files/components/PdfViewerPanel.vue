<template>
  <section class="pdf-viewer column no-wrap fit" aria-label="PDF-Vorschau">
    <div v-if="loading" class="col flex flex-center"><q-spinner color="primary" size="36px" /></div>
    <AppStateMessage v-else-if="failed" class="col q-pa-md" icon="picture_as_pdf" title="PDF konnte nicht geladen werden">
      <template #actions><q-btn no-caps color="primary" label="Erneut versuchen" @click="load" /></template>
    </AppStateMessage>
    <template v-else-if="url">
      <object :data="url" type="application/pdf" class="col full-width" :aria-label="`PDF: ${name}`">
        <p class="q-pa-md">Dein Browser unterstützt keine eingebettete PDF-Vorschau. Öffne die PDF separat oder lade sie herunter.</p>
      </object>
      <div class="row justify-end q-pt-xs">
        <q-btn flat no-caps icon="open_in_new" label="PDF separat öffnen" :href="url" target="_blank" rel="noopener noreferrer" />
      </div>
    </template>
  </section>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { api } from 'src/boot/axios'
import AppStateMessage from 'src/shared/ui/AppStateMessage.vue'

const props = defineProps({ src: { type: String, required: true }, name: { type: String, default: 'Dokument' } })
const loading = ref(false)
const failed = ref(false)
const url = ref('')
let controller

async function load() {
  controller?.abort()
  const request = new AbortController()
  controller = request
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = ''
  loading.value = true
  failed.value = false
  try {
    const blob = props.src.startsWith('blob:')
      ? await fetch(props.src, { signal: request.signal }).then((response) => response.blob())
      : (await api.get(props.src.replace(/^\/api\//u, '/'), { responseType: 'blob', signal: request.signal })).data
    if (!request.signal.aborted) url.value = URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }))
  } catch {
    if (!request.signal.aborted) failed.value = true
  } finally {
    if (!request.signal.aborted) loading.value = false
  }
}

watch(() => props.src, load, { immediate: true })
onBeforeUnmount(() => {
  controller?.abort()
  if (url.value) URL.revokeObjectURL(url.value)
})
</script>

<style scoped>
.pdf-viewer,
.pdf-viewer object {
  min-height: 0;
}
</style>
