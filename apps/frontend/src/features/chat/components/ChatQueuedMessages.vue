<template>
  <div
    v-if="messages.length"
    class="chat-queued"
    aria-label="Vorgemerkte Nachrichten"
  >
    <q-card flat class="app-surface-tonal chat-queued__card">
      <div class="row items-center q-gutter-sm q-mb-xs">
        <q-icon :name="paused ? 'error_outline' : 'schedule_send'" :color="paused ? 'negative' : undefined" size="18px" />
        <span class="text-caption text-weight-medium" role="status" aria-live="polite" aria-atomic="true">
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
      <div class="chat-queued__items">
        <article
          v-for="(message, index) in messages"
          :key="message.id"
          class="chat-queued__item row items-start no-wrap q-gutter-sm"
        >
          <div class="col chat-queued__text">
            {{ message.content || 'Nachricht ohne Text' }}
            <div v-if="message.attachments.length" class="text-caption app-muted">
              {{ attachmentLabel(message) }}
            </div>
          </div>
          <q-btn
            flat
            round
            dense
            size="sm"
            icon="undo"
            :loading="message.id === sendingId"
            :disable="message.id === sendingId"
            :aria-label="`Vorgemerkte Nachricht ${index + 1} rückgängig machen`"
            @click="$emit('undo', message.id)"
          >
            <q-tooltip>Vorgemerkte Nachricht rückgängig machen</q-tooltip>
          </q-btn>
        </article>
      </div>
    </q-card>
  </div>
</template>

<script setup>
import { computed } from 'vue'

defineOptions({ name: 'ChatQueuedMessages' })

const props = defineProps({
  messages: { type: Array, default: () => [] },
  paused: Boolean,
  sendingId: { type: String, default: null }
})

defineEmits(['resume', 'undo'])

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
.chat-queued {
  width: min(900px, 100%);
  margin: 0 auto;
  padding: 8px 16px 0;
}

.chat-queued__card {
  padding: 8px 12px;
  border-radius: 12px;
}

.chat-queued__item + .chat-queued__item {
  margin-top: 4px;
}

.chat-queued__items {
  max-height: min(28dvh, 240px);
  overflow-y: auto;
  overscroll-behavior-y: contain;
}

.chat-queued__text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

@media (max-width: 700px) {
  .chat-queued {
    padding: 8px 8px 0;
  }
}
</style>
