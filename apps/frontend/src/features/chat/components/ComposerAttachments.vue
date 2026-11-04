<template>
  <div v-if="attachments.length || $slots.default" class="composer-attachments" aria-label="Ausgewählte Anhänge">
    <slot />
    <ComposerItemCard
      v-for="attachment in attachments"
      :key="attachment.id"
      class="composer-attachment"
      :class="{ 'composer-attachment--image': attachment.kind === 'image', 'composer-attachment--audio': attachment.kind === 'audio' }"
      :disabled="disabled"
      :remove-label="`${attachment.file.name || 'Anhang'} entfernen`"
      @remove="$emit('remove', attachment.id)"
    >
      <template v-if="attachment.kind === 'image'">
        <img v-if="attachment.objectUrl" :src="attachment.objectUrl" :alt="attachment.file.name || 'Bildanhang'" class="composer-attachment__preview">
        <div v-else class="composer-attachment__icon"><q-icon name="image" size="26px" /></div>
        <q-tooltip>{{ attachment.file.name || 'Bild' }} · {{ formatAttachmentSize(attachment.file.size) }}</q-tooltip>
      </template>
      <template v-else>
        <div v-if="attachment.kind !== 'audio'" class="composer-attachment__icon"><q-icon :name="attachmentDraftIcon(attachment)" size="26px" /></div>
        <div class="composer-attachment__body">
          <strong class="ellipsis">{{ attachment.file.name || (attachment.kind === 'audio' ? 'Audioanhang' : 'Datei') }}</strong>
          <span class="text-caption app-muted ellipsis">{{ attachmentDraftLabel(attachment) }} · {{ formatAttachmentSize(attachment.file.size) }}</span>
          <span v-if="attachment.kind === 'audio' && attachment.voiceMessage" class="text-caption app-muted">Transkription beim Senden</span>
          <audio v-if="attachment.kind === 'audio' && attachment.objectUrl" :src="attachment.objectUrl" controls preload="metadata" class="full-width q-mt-xs" :aria-label="`${attachment.file.name || 'Audioanhang'} vorhören`" />
        </div>
      </template>
    </ComposerItemCard>
  </div>
</template>

<script setup>
import ComposerItemCard from './ComposerItemCard.vue'
import { attachmentDraftIcon, attachmentDraftLabel } from 'src/features/chat/attachment-drafts'
import { formatAttachmentSize } from 'src/features/files/attachments'

defineOptions({ name: 'ComposerAttachments' })

defineProps({
  attachments: { type: Array, default: () => [] },
  disabled: Boolean
})

defineEmits(['remove'])
</script>
