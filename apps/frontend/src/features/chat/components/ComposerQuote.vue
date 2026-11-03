<template>
  <ComposerItemCard
    v-bind="$attrs" class="composer-quote chat-quote-typography" :class="{ 'chat-messages--small': messageSize === 'small', 'full-width': !editable }"
    :disabled="disabled" :removable="editable && !editing" remove-label="Zitat entfernen" @remove="$emit('remove')"
  >
    <div class="col q-pa-xs">
      <div v-if="editable" class="q-pr-xl">
        <div class="row no-wrap items-center q-pa-xs">
          <q-icon name="format_quote" size="1.2em" class="q-mr-xs" role="img" aria-label="Zitat" />
          <span class="col ellipsis">{{ quote.text.replace(/\s+/gu, ' ') }}</span>
        </div>
      </div>
      <blockquote v-if="!editable" class="composer-quote__text overflow-auto q-my-xs q-mx-none q-pl-sm">{{ quote.text }}</blockquote>
      <div v-if="quote.comment" class="composer-quote__comment q-px-xs q-pb-xs" :class="{ ellipsis: editable }">{{ quote.comment }}</div>
    </div>
    <template v-if="editable && !editing" #actions>
      <q-btn flat round dense icon="edit" size="sm" type="button" aria-label="Zitat bearbeiten" :disable="disabled" @click="editing = true" />
    </template>
    <AppMenu v-if="editable && !mobile" v-model="editing" no-parent-event no-refocus anchor="top left" self="bottom left" max-height="min(45dvh, 360px)">
      <QuoteEditor v-if="editing" class="chat-quote-popover q-pa-sm" :quote="quote" show-text :message-size="messageSize" :disabled="disabled" @save="save" @cancel="editing = false" />
    </AppMenu>
  </ComposerItemCard>
  <q-dialog v-if="mobile" v-model="editing" :transition-duration="0">
    <q-card class="composer-quote-editor">
      <q-card-section class="text-subtitle1">Zitat bearbeiten</q-card-section>
      <QuoteEditor v-if="editing" class="q-px-md q-pb-md" :quote="quote" show-text :message-size="messageSize" :disabled="disabled" @save="save" @cancel="editing = false" />
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import AppMenu from 'src/shared/ui/AppMenu.vue'
import ComposerItemCard from './ComposerItemCard.vue'
import QuoteEditor from './QuoteEditor.vue'

defineOptions({ inheritAttrs: false })
const props = defineProps({
  quote: { type: Object, required: true },
  editable: { type: Boolean, default: true },
  disabled: Boolean,
  messageSize: { type: String, default: 'standard' }
})
const emit = defineEmits(['update', 'remove', 'editing'])
const $q = useQuasar()
const mobile = computed(() => $q.screen.lt.sm)
const editing = ref(false)
watch(() => props.quote, () => { editing.value = false })
watch(editing, value => emit('editing', value))
onBeforeUnmount(() => { if (editing.value) emit('editing', false) })
function save(quote) {
  emit('update', quote)
  editing.value = false
}
</script>
