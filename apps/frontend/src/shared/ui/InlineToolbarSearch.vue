<template>
  <div class="inline-toolbar-search row items-center no-wrap col">
    <q-input
      v-if="searchOpen"
      ref="searchInput"
      :model-value="modelValue"
      class="col"
      borderless
      dense
      clearable
      :placeholder="placeholder"
      :aria-label="placeholder"
      :disable="disable"
      @update:model-value="$emit('update:modelValue', $event || '')"
      @keydown.esc.stop.prevent="closeSearch"
    />
    <slot v-else />
    <q-btn
      flat
      round
      dense
      :icon="searchOpen ? 'close' : 'search'"
      :aria-label="searchOpen ? 'Suche schließen' : searchLabel"
      :disable="disable"
      @click="toggleSearch"
    />
  </div>
</template>

<script setup>
import { nextTick, ref } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: 'Geladene Einträge filtern' },
  searchLabel: { type: String, default: 'Suche öffnen' },
  disable: Boolean
})
const emit = defineEmits(['update:modelValue'])
const searchInput = ref(null)
const searchOpen = ref(Boolean(props.modelValue))

async function toggleSearch() {
  if (searchOpen.value) {
    closeSearch()
    return
  }
  searchOpen.value = true
  await nextTick()
  searchInput.value?.focus()
}

function closeSearch() {
  emit('update:modelValue', '')
  searchOpen.value = false
}
</script>

<style scoped>
.inline-toolbar-search,
.inline-toolbar-search :deep(.q-field) {
  min-width: 0;
}
</style>
