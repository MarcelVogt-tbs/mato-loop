<template>
  <div v-if="attachments.length" class="composer-attachments" aria-label="Ausgewählte Anhänge">
    <article
      v-for="attachment in attachments"
      :key="attachment.id"
      class="composer-attachment app-surface-tonal"
      :class="{ 'composer-attachment--image': attachment.kind === 'image' }"
    >
      <img
        v-if="attachment.kind === 'image' && attachment.objectUrl"
        :src="attachment.objectUrl"
        :alt="attachment.file.name || 'Bildanhang'"
        class="composer-attachment__preview"
      >
      <div v-else class="composer-attachment__icon">
        <q-icon :name="attachmentDraftIcon(attachment)" size="26px" />
      </div>
      <div v-if="attachment.kind !== 'image'" class="composer-attachment__body">
        <strong class="ellipsis">{{ attachment.file.name || 'Datei' }}</strong>
        <span class="text-caption app-muted ellipsis">
          {{ attachmentDraftLabel(attachment) }} · {{ formatAttachmentSize(attachment.file.size) }}
        </span>
      </div>
      <q-btn
        flat
        round
        dense
        icon="close"
        size="sm"
        class="composer-attachment__remove"
        :aria-label="`${attachment.file.name || 'Anhang'} entfernen`"
        :disable="disabled"
        @click="$emit('remove', attachment.id)"
      >
        <q-tooltip>Anhang entfernen</q-tooltip>
      </q-btn>
      <q-tooltip v-if="attachment.kind === 'image'">
        {{ attachment.file.name || 'Bild' }} · {{ formatAttachmentSize(attachment.file.size) }}
      </q-tooltip>
    </article>
  </div>
</template>

<script setup>
import { attachmentDraftIcon, attachmentDraftLabel } from 'src/utils/attachment-drafts'
import { formatAttachmentSize } from 'src/utils/attachments'

defineOptions({ name: 'ComposerAttachments' })

defineProps({
  attachments: { type: Array, default: () => [] },
  disabled: Boolean
})

defineEmits(['remove'])
</script>
