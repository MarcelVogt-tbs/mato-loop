<template>
  <main class="markdown-print-page">
    <q-toolbar class="markdown-print__toolbar q-px-md">
      <q-toolbar-title class="text-subtitle1 text-weight-bold ellipsis">
        {{ title }}
      </q-toolbar-title>
      <q-btn
        unelevated
        no-caps
        color="primary"
        icon="print"
        label="Drucken / als PDF speichern"
        :disable="!ready"
        @click="printDocument"
      />
    </q-toolbar>
    <q-separator class="markdown-print__toolbar" />

    <div v-if="loading" class="markdown-print__state flex flex-center">
      <q-spinner color="primary" size="40px" />
    </div>
    <div v-else-if="errorMessage" class="markdown-print__state column flex-center text-center q-pa-xl" role="alert">
      <q-icon name="error_outline" color="negative" size="56px" class="q-mb-md" />
      <div class="text-subtitle1 text-weight-medium">PDF-Ausgabe nicht möglich</div>
      <div class="q-mt-xs">{{ errorMessage }}</div>
    </div>
    <article v-else class="markdown-print__document">
      <MessageContent
        :content="preview.content"
        :allow-images="false"
        :interactive="false"
        force-light
        @rendered="handleRendered"
      />
    </article>
  </main>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import MessageContent from 'components/MessageContent.vue'
import { api } from 'src/boot/axios'
import { apiErrorMessage } from 'src/utils/api-errors'
import { markdownPrintRequest } from 'src/utils/markdown-print'

defineOptions({ name: 'MarkdownPrintPage' })

const route = useRoute()
const loading = ref(true)
const ready = ref(false)
const preview = ref({ content: '' })
const errorMessage = ref('')
const request = markdownPrintRequest(route.query)
const filename = request?.params.path.split('/').pop() || 'Markdown-Dokument'
const title = computed(() => filename)
let printStarted = false

async function loadDocument() {
  if (!request) {
    errorMessage.value = 'Die Druckansicht enthält keine gültige Dateireferenz.'
    loading.value = false
    return
  }
  window.history.replaceState({}, '', route.path)
  try {
    const { data } = await api.get('/workspace/files/preview', {
      params: request.params,
      suppressErrorToast: true
    })
    if (data.kind !== 'markdown') {
      errorMessage.value = 'Die ausgewählte Datei ist keine Markdown-Datei.'
      return
    }
    if (request.expectedRevision && data.revision !== request.expectedRevision) {
      errorMessage.value = 'Die Datei wurde seit dem Öffnen geändert. Öffne die Vorschau erneut und starte den Export noch einmal.'
      return
    }
    if (document.fonts?.ready) await document.fonts.ready
    document.title = filename.replace(/\.(?:md|markdown)$/i, '') || filename
    preview.value = data
  } catch (error) {
    errorMessage.value = apiErrorMessage(error, 'Die Markdown-Datei konnte nicht geladen werden.')
  } finally {
    loading.value = false
  }
}

async function handleRendered() {
  ready.value = true
  if (printStarted) return
  printStarted = true
  await nextTick()
  requestAnimationFrame(() => requestAnimationFrame(printDocument))
}

function printDocument() {
  if (ready.value) window.print()
}

function afterPrint() {
  printStarted = false
}

onMounted(() => {
  window.addEventListener('afterprint', afterPrint)
  loadDocument()
})
onBeforeUnmount(() => window.removeEventListener('afterprint', afterPrint))
</script>
