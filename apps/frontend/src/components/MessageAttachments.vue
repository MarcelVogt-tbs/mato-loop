<template>
  <div v-if="items.length" class="message-attachments">
    <div v-if="label" class="tool-call__section-label">{{ label }}</div>

    <div v-if="images.length" class="attachment-grid">
      <button
        v-for="attachment in images"
        :key="attachment.id"
        type="button"
        class="attachment-preview app-surface"
        :disabled="!attachment.safeUrl || loadErrors[attachment.id]"
        :aria-label="`${attachment.filename || 'Bild'} vergrößern`"
        @click="openAttachment(attachment)"
      >
        <img
          v-if="attachment.safeUrl && !loadErrors[attachment.id]"
          :src="attachment.safeUrl"
          :alt="attachment.filename || 'Bildanhang'"
          loading="lazy"
          decoding="async"
          @error="loadErrors[attachment.id] = true"
        >
        <div v-else class="attachment-preview__error">
          <q-icon name="broken_image" size="28px" />
          <span>Bild konnte nicht geladen werden.</span>
        </div>
        <span class="attachment-preview__meta">
          <strong>{{ attachment.filename || 'Bild' }}</strong>
          <small>{{ attachmentMetadata(attachment) }}</small>
        </span>
      </button>
    </div>

    <div v-if="audio.length" class="audio-attachment-list">
      <div v-for="attachment in audio" :key="attachment.id" class="audio-attachment app-surface">
        <div class="audio-attachment__header row items-start no-wrap q-gutter-sm">
          <q-icon name="audio_file" color="primary" size="24px" />
          <div class="col ellipsis">
            <div class="text-weight-medium ellipsis">{{ attachment.filename || 'Audioaufnahme' }}</div>
            <div class="text-caption app-muted ellipsis">{{ attachmentMetadata(attachment) }}</div>
          </div>
          <q-btn
            v-if="attachment.safeUrl"
            flat
            round
            dense
            icon="download"
            :href="attachment.safeUrl"
            :download="attachment.filename || 'audio.wav'"
            aria-label="Audio herunterladen"
          >
            <q-tooltip>Herunterladen</q-tooltip>
          </q-btn>
        </div>
        <audio v-if="attachment.safeUrl && audioMimeTypeSupported(attachment.mimeType)" controls preload="metadata">
          <source :src="attachment.safeUrl" :type="attachment.mimeType">
          Dein Browser kann diese Audiodatei nicht wiedergeben.
        </audio>
        <q-banner v-else-if="!attachment.safeUrl" dense rounded class="app-surface-tonal text-caption q-mt-sm">
          Die Audiodatei ist nicht über eine sichere interne Adresse verfügbar.
        </q-banner>
        <div v-else class="text-caption app-muted q-mt-sm">
          Dein Browser unterstützt die Wiedergabe dieses Audioformats nicht. Du kannst die Datei herunterladen.
        </div>
      </div>
    </div>

    <div v-if="files.length" class="file-attachment-list">
      <component
        v-for="attachment in files"
        :key="attachment.id"
        :is="attachment.safeUrl ? 'a' : 'article'"
        class="file-attachment app-surface"
        :class="{ 'file-attachment--download': attachment.safeUrl }"
        :href="attachment.safeUrl || undefined"
        :download="attachment.safeUrl ? attachment.filename || 'datei' : undefined"
        :aria-label="attachment.safeUrl ? `${attachment.filename || 'Datei'} herunterladen` : undefined"
      >
        <div class="file-attachment__icon">
          <q-icon :name="attachmentIcon(attachment)" size="25px" />
        </div>
        <div class="file-attachment__body">
          <div class="text-weight-medium ellipsis">{{ attachment.filename || 'Dateianhang' }}</div>
          <div class="text-caption app-muted ellipsis">{{ attachmentMetadata(attachment) }}</div>
        </div>
        <q-icon v-if="attachment.safeUrl" name="download" size="24px" aria-hidden="true" />
        <q-icon v-else name="link_off" class="app-muted" size="20px">
          <q-tooltip>Keine sichere interne Downloadadresse verfügbar</q-tooltip>
        </q-icon>
      </component>
    </div>

    <q-dialog v-model="attachmentDialog" :maximized="$q.screen.lt.sm">
      <q-card v-if="selectedAttachment" class="attachment-dialog app-surface">
        <q-card-section class="row items-start no-wrap q-gutter-md">
          <div class="col">
            <div class="text-h6 ellipsis">{{ selectedAttachment.filename || 'Bildvorschau' }}</div>
            <div class="text-caption app-muted">{{ attachmentMetadata(selectedAttachment) }}</div>
          </div>
          <q-btn flat round dense icon="close" aria-label="Bildvorschau schließen" v-close-popup />
        </q-card-section>
        <q-separator />
        <q-card-section class="attachment-dialog__body">
          <img
            :src="selectedAttachment.safeUrl"
            :alt="selectedAttachment.filename || 'Bildanhang in Großansicht'"
            @error="closeFailedAttachment"
          >
        </q-card-section>
      </q-card>
    </q-dialog>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import {
  attachmentIcon,
  attachmentMetadata,
  audioMimeTypeSupported,
  supportedAttachments
} from 'src/utils/attachments'

defineOptions({ name: 'MessageAttachments' })

const props = defineProps({
  attachments: { type: Array, default: () => [] },
  label: { type: String, default: '' }
})

const items = computed(() => supportedAttachments(props.attachments))
const images = computed(() => items.value.filter((attachment) => attachment.kind === 'image'))
const audio = computed(() => items.value.filter((attachment) => attachment.kind === 'audio'))
const files = computed(() => items.value.filter((attachment) => (
  ['text', 'document', 'file'].includes(attachment.kind)
)))
const loadErrors = reactive({})
const attachmentDialog = ref(false)
const selectedAttachment = ref(null)

function openAttachment(attachment) {
  if (!attachment.safeUrl || loadErrors[attachment.id]) return
  selectedAttachment.value = attachment
  attachmentDialog.value = true
}

function closeFailedAttachment() {
  if (selectedAttachment.value) loadErrors[selectedAttachment.value.id] = true
  attachmentDialog.value = false
}
</script>
