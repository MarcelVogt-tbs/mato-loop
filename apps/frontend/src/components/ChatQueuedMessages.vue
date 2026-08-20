<template>
  <div v-if="messages.length" class="chat-queued q-px-md q-pt-sm" aria-label="Vorgemerkte Nachrichten">
    <q-card flat class="app-surface-tonal chat-queued__card">
      <div class="row items-center q-gutter-sm q-mb-xs">
        <q-icon :name="paused ? 'error_outline' : 'schedule_send'" :color="paused ? 'negative' : undefined" size="18px" />
        <span class="text-caption text-weight-medium">
          {{ headline }}
        </span>
        <q-space />
        <q-btn
          v-if="paused"
          flat
          dense
          no-caps
          size="sm"
          icon="refresh"
          label="Erneut versuchen"
          @click="$emit('resume')"
        />
      </div>
      <article
        v-for="message in messages"
        :key="message.id"
        class="chat-queued__item row items-start no-wrap q-gutter-sm"
      >
        <div class="col chat-queued__text">
          {{ message.content }}
          <div v-if="message.attachments.length" class="text-caption app-muted">
            {{ attachmentLabel(message) }}
          </div>
        </div>
        <q-btn
          flat
          round
          dense
          size="sm"
          icon="close"
          aria-label="Vorgemerkte Nachricht entfernen"
          @click="$emit('remove', message.id)"
        >
          <q-tooltip>Vorgemerkte Nachricht entfernen</q-tooltip>
        </q-btn>
      </article>
    </q-card>
  </div>
</template>

<script setup>
import { computed } from 'vue'

defineOptions({ name: 'ChatQueuedMessages' })

const props = defineProps({
  messages: { type: Array, default: () => [] },
  paused: Boolean
})

defineEmits(['remove', 'resume'])

const headline = computed(() => {
  if (props.paused) return 'Die vorgemerkte Nachricht konnte nicht gesendet werden.'
  return props.messages.length === 1
    ? 'Wird gesendet, sobald der Chat bereit ist'
    : `${props.messages.length} Nachrichten werden gesendet, sobald der Chat bereit ist`
})

function attachmentLabel(message) {
  const count = message.attachments.length
  return count === 1 ? '1 Anhang' : `${count} Anhänge`
}
</script>

<style scoped>
.chat-queued__card {
  padding: 8px 12px;
  border-radius: 12px;
}

.chat-queued__item + .chat-queued__item {
  margin-top: 4px;
}

.chat-queued__text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
