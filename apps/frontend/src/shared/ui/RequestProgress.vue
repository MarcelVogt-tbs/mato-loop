<template>
  <component :is="numbered ? 'div' : QCardSection" class="row items-center" :class="numbered ? 'justify-end' : 'request-progress'">
    <div v-if="!numbered && items.length > 1" class="row items-center no-wrap q-gutter-sm">
      <q-icon v-if="icon" :name="icon" color="primary" size="20px" />
      <span class="text-subtitle2 text-weight-bold">{{ label }} {{ index + 1 }} von {{ items.length }}</span>
    </div>
    <div v-if="numbered && items.length > 1" class="row q-gutter-xs">
      <q-btn
        v-for="(item, position) in items"
        :key="item.id"
        dense
        :outline="position !== index"
        :unelevated="position === index"
        :color="item.answered && position !== index ? 'positive' : 'primary'"
        :label="position + 1"
        :icon-right="item.answered ? 'check' : undefined"
        :disable="disabled"
        :aria-label="`${label} ${position + 1}: ${item.label}${item.answered ? ', beantwortet' : ''}`"
        :aria-current="position === index ? 'step' : undefined"
        @click="emit('select', position)"
      />
    </div>
    <div v-else-if="items.length > 1" class="request-progress__bars row no-wrap">
      <button
        v-for="(item, position) in items"
        :key="item.id"
        type="button"
        class="request-progress__segment"
        :class="{
          'request-progress__segment--active': position === index,
          'request-progress__segment--answered': item.answered
        }"
        :disabled="disabled"
        :aria-label="`${label} ${position + 1}: ${item.label}`"
        :aria-current="position === index ? 'step' : undefined"
        @click="emit('select', position)"
      >
        <q-tooltip>{{ item.label }}</q-tooltip>
      </button>
    </div>
    <q-space v-if="!numbered" class="request-progress__spacer" />
    <slot />
  </component>
</template>

<script setup>
import { QCardSection } from 'quasar'

defineProps({
  label: { type: String, required: true },
  icon: { type: String, default: '' },
  items: { type: Array, required: true },
  index: { type: Number, required: true },
  disabled: Boolean,
  numbered: Boolean
})
const emit = defineEmits(['select'])
</script>
