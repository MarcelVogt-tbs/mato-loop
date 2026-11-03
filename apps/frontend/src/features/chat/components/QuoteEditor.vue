<template>
  <div class="chat-quote-typography" :class="{ 'chat-messages--small': messageSize === 'small' }" @keydown.esc.stop.prevent="$emit('cancel')">
    <q-input
      v-if="showText" v-model="text" outlined dense autogrow type="textarea" label="Zitat"
      input-class="chat-quote-editor__input" :disable="disabled"
    />
    <q-input
      v-model="comment" outlined dense autogrow autofocus type="textarea" rows="1" label="Kommentar (optional)"
      :class="{ 'q-mt-sm': showText }" input-class="chat-quote-editor__input" :disable="disabled"
      aria-label="Kommentar zum Zitat" @keydown.enter.exact.stop.prevent="save"
    />
    <div class="row justify-end q-gutter-xs q-mt-xs">
      <q-btn flat round icon="close" type="button" aria-label="Zitieren abbrechen" :disable="disabled" @click="$emit('cancel')" />
      <q-btn flat round color="primary" icon="check" type="button" :disable="disabled || !text.trim()" aria-label="Zitat in den Entwurf übernehmen" @click="save" />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  quote: { type: Object, required: true },
  showText: Boolean,
  disabled: Boolean,
  messageSize: { type: String, default: 'standard' }
})
const emit = defineEmits(['save', 'cancel'])
const text = ref(props.quote.text)
const comment = ref(props.quote.comment || '')
function save(event) {
  if (event?.isComposing || props.disabled || !text.value.trim()) return
  emit('save', { text: text.value, comment: comment.value })
}
</script>
