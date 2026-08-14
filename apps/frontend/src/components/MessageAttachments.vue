<template>
  <div v-if="items.length" class="message-attachments">
    <div v-if="label" class="tool-call__section-label">{{ label }}</div>

    <div v-if="images.length" class="attachment-grid">
      <article
        v-for="attachment in images"
        :key="attachment.id"
        class="attachment-preview app-surface"
      >
        <button
          type="button"
          class="attachment-preview__image"
          :disabled="!attachment.safeUrl || loadErrors[attachment.id]"
          :aria-label="`${attachment.filename || 'Bild'} im Vollbild öffnen`"
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
        </button>
        <div class="attachment-preview__meta">
          <strong class="ellipsis" :title="attachment.filename || 'Bild'">{{ attachment.filename || 'Bild' }}</strong>
          <div v-if="attachment.safeUrl" class="attachment-preview__actions row items-center no-wrap">
            <q-btn
              flat
              round
              dense
              icon="open_in_new"
              :href="attachment.safeUrl"
              target="_blank"
              rel="noopener noreferrer"
              :aria-label="`${attachment.filename || 'Bild'} in neuem Tab öffnen`"
            >
              <q-tooltip>In neuem Tab öffnen</q-tooltip>
            </q-btn>
            <q-btn
              flat
              round
              dense
              icon="download"
              :href="attachment.safeUrl"
              :download="attachment.filename || 'bild'"
              :aria-label="`${attachment.filename || 'Bild'} herunterladen`"
            >
              <q-tooltip>Herunterladen</q-tooltip>
            </q-btn>
          </div>
        </div>
      </article>
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

    <div v-if="videos.length" class="video-attachment-list">
      <div v-for="attachment in videos" :key="attachment.id" class="video-attachment app-surface">
        <div class="row items-start no-wrap q-gutter-sm">
          <q-icon name="video_file" color="primary" size="24px" />
          <div class="col ellipsis">
            <div class="text-weight-medium ellipsis">{{ attachment.filename || 'Video' }}</div>
            <div class="text-caption app-muted ellipsis">{{ attachmentMetadata(attachment) }}</div>
          </div>
          <q-btn
            v-if="attachment.safeUrl"
            flat
            round
            dense
            icon="download"
            :href="attachment.safeUrl"
            :download="attachment.filename || 'video'"
            aria-label="Video herunterladen"
          >
            <q-tooltip>Herunterladen</q-tooltip>
          </q-btn>
        </div>
        <video v-if="attachment.safeUrl && videoMimeTypeSupported(attachment.mimeType)" controls preload="metadata" playsinline>
          <source :src="attachment.safeUrl" :type="attachment.mimeType">
          Dein Browser kann diese Videodatei nicht wiedergeben.
        </video>
        <q-banner v-else-if="!attachment.safeUrl" dense rounded class="app-surface-tonal text-caption q-mt-sm">
          Die Videodatei ist nicht über eine sichere interne Adresse verfügbar.
        </q-banner>
        <div v-else class="text-caption app-muted q-mt-sm">
          Dein Browser unterstützt die Wiedergabe dieses Videoformats nicht. Du kannst die Datei herunterladen.
        </div>
      </div>
    </div>

    <div v-if="files.length" class="file-attachment-list">
      <article
        v-for="attachment in files"
        :key="attachment.id"
        class="file-attachment app-surface"
      >
        <div class="file-attachment__icon">
          <q-icon :name="attachmentIcon(attachment)" size="25px" />
        </div>
        <div class="file-attachment__body">
          <div class="text-weight-medium ellipsis">{{ attachment.filename || 'Dateianhang' }}</div>
          <div class="text-caption app-muted ellipsis">{{ attachmentMetadata(attachment) }}</div>
        </div>
        <q-btn
          v-if="attachment.safeUrl && isStl(attachment)"
          flat
          dense
          no-caps
          color="primary"
          icon="view_in_ar"
          label="Ansehen"
          :aria-label="`${attachment.filename || '3D-Modell'} ansehen`"
          @click="openAttachment(attachment)"
        />
        <q-btn
          v-if="attachment.safeUrl"
          flat
          round
          dense
          icon="download"
          :href="attachment.safeUrl"
          :download="attachment.filename || 'datei'"
          :aria-label="`${attachment.filename || 'Datei'} herunterladen`"
        >
          <q-tooltip>Herunterladen</q-tooltip>
        </q-btn>
        <q-icon v-else name="link_off" class="app-muted" size="20px">
          <q-tooltip>Keine sichere interne Downloadadresse verfügbar</q-tooltip>
        </q-icon>
      </article>
    </div>

    <q-dialog v-model="attachmentDialog" maximized transition-show="fade" transition-hide="fade" @hide="selectedAttachment = null">
      <q-card v-if="selectedAttachment" class="app-file-dialog">
        <FileViewerPanel
          :image="selectedAttachment.kind === 'image' ? { url: selectedAttachment.safeUrl, name: selectedAttachment.filename || 'Bild' } : null"
          :model="isStl(selectedAttachment) ? { url: selectedAttachment.safeUrl, name: selectedAttachment.filename || '3D-Modell' } : null"
          fullscreen
          @close="attachmentDialog = false"
          @toggle-fullscreen="attachmentDialog = false"
        />
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
  supportedAttachments,
  videoMimeTypeSupported
} from 'src/utils/attachments'
import FileViewerPanel from 'components/FileViewerPanel.vue'

defineOptions({ name: 'MessageAttachments' })

const props = defineProps({
  attachments: { type: Array, default: () => [] },
  label: { type: String, default: '' }
})

const items = computed(() => supportedAttachments(props.attachments))
const images = computed(() => items.value.filter((attachment) => attachment.kind === 'image'))
const audio = computed(() => items.value.filter((attachment) => attachment.kind === 'audio'))
const videos = computed(() => items.value.filter((attachment) => attachment.kind === 'video'))
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

function isStl(attachment) {
  return attachment?.mimeType === 'model/stl'
    || attachment?.filename?.toLocaleLowerCase().endsWith('.stl')
}

</script>
