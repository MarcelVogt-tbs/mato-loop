<template>
  <div class="q-gutter-sm">
    <div class="row items-center no-wrap">
      <div class="text-subtitle2 col">Signatur (optional)</div>
      <q-btn flat round dense icon="info_outline" aria-label="Hinweise zur E-Mail-Signatur" @click="infoOpen = true" />
    </div>
    <q-editor
      ref="editor"
      v-model="content"
      aria-label="Formatierte E-Mail-Signatur"
      min-height="6rem"
      max-height="24rem"
      :toolbar="[['bold', 'italic', 'underline'], ['unordered', 'ordered'], ['link'], ['removeFormat']]"
      class="mail-signature-editor"
      @paste.stop="pasteSignature"
    />
    <q-file
      v-model="image"
      dense outlined clearable
      accept="image/png,image/jpeg,image/webp"
      label="Signaturbild einfügen"
      :loading="loading"
      :disable="loading"
      @update:model-value="insertImage"
    >
      <template #prepend><q-icon name="image" /></template>
    </q-file>
    <q-dialog v-model="infoOpen" :transition-duration="0" :maximized="$q.screen.lt.sm" aria-labelledby="signature-info-title">
      <q-card class="app-surface column no-wrap full-width" style="max-width: 32rem">
        <q-card-section class="row items-center no-wrap">
          <h2 id="signature-info-title" class="text-h6 col q-my-none">E-Mail-Signatur</h2>
          <q-btn v-close-popup flat round dense icon="close" aria-label="Dialog schließen" />
        </q-card-section>
        <q-separator />
        <q-card-section class="col scroll">
          <p>Füge deine Signatur aus Outlook oder Word direkt ein. Tabellen, Farben und Abstände werden übernommen.</p>
          <p>Fehlende Bilder kannst du mit „Signaturbild einfügen“ an der gewünschten Stelle ergänzen. Bilder werden direkt eingebettet; externe Bilder werden nicht nachgeladen.</p>
          <p>PNG, JPEG oder WebP: je 200 KiB, zusammen 400 KiB, höchstens 8 Bilder und 2048 Pixel je Seite.</p>
          <p class="q-mb-none">Die Signatur wird neuen E-Mails und Antwortentwürfen angefügt. Vorhandene Entwürfe bleiben unverändert.</p>
        </q-card-section>
      </q-card>
    </q-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { QEditor, useQuasar } from 'quasar'
import { prepareSignature, safeSignature } from './signature'

const props = defineProps({ modelValue: { type: String, default: '' } })
const emit = defineEmits(['update:modelValue'])
const $q = useQuasar()
const editor = ref(null)
const image = ref(null)
const loading = ref(false)
const infoOpen = ref(false)
const content = computed({
  get: () => safeSignature(props.modelValue),
  set: (value) => emit('update:modelValue', safeSignature(value))
})

function pasteSignature(event) {
  const html = event.clipboardData?.getData('text/html')
  if (!html) return
  event.preventDefault()
  const prepared = prepareSignature(html)
  editor.value.runCmd('insertHTML', prepared.html, true)
  if (prepared.missingImages) {
    $q.notify({
      type: 'warning',
      message: 'Nicht alle Bilder wurden mitgeliefert. Ergänze sie über „Signaturbild einfügen“. Externe Bilder werden nicht nachgeladen.',
      timeout: 8000
    })
  }
}

async function insertImage(file) {
  if (!file) return
  const document = new DOMParser().parseFromString(content.value, 'text/html')
  const images = [...document.querySelectorAll('img')]
  const total = images.reduce((size, item) => size + (item.getAttribute('src')?.split(',')[1]?.length || 0) * 3 / 4, file.size)
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 200 * 1024 || images.length >= 8 || total > 400 * 1024) {
    $q.notify({ type: 'warning', message: 'Das Signaturbild überschreitet das Format- oder Größenlimit.' })
    image.value = null
    return
  }
  loading.value = true
  try {
    const source = await new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result)
      reader.onerror = reject
      reader.readAsDataURL(file)
    })
    editor.value.runCmd('insertHTML', `<img src="${source}" alt="Signaturbild" width="160">`, true)
  } catch {
    $q.notify({ type: 'negative', message: 'Das Signaturbild konnte nicht gelesen werden.' })
  } finally {
    loading.value = false
    image.value = null
  }
}
</script>

<style scoped>
.mail-signature-editor {
  min-width: 0;
  max-width: 100%;
}

.mail-signature-editor :deep(.q-editor__content) {
  overflow: auto;
  background: #fff;
  color: #000;
}

.mail-signature-editor :deep(img) {
  max-width: 100%;
  height: auto;
}
</style>
