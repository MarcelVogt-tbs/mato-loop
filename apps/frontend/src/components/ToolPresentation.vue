<template>
  <q-list
    v-if="items.length"
    dense
    :bordered="!embedded"
    :separator="!embedded"
    :class="{ 'rounded-borders': !embedded }"
  >
    <q-item
      v-for="(item, index) in items"
      :key="`${item.label}:${index}`"
      :class="{ 'q-pa-none': embedded, 'q-mt-md': embedded && index > 0 }"
    >
      <q-item-section>
        <q-item-label caption :class="{ 'app-muted': embedded }">{{ item.label }}</q-item-label>
        <pre v-if="item.format !== 'text'" class="tool-call__details q-mt-xs">{{ itemText(item) }}</pre>
        <q-item-label v-else class="text-body2">{{ item.value }}</q-item-label>
      </q-item-section>
    </q-item>
  </q-list>
</template>

<script setup>
defineOptions({ name: 'ToolPresentation' })

defineProps({
  items: { type: Array, required: true },
  embedded: Boolean
})

function itemText(item) {
  if (!Array.isArray(item.value)) return item.value
  return item.value.length ? item.value.map((value) => `- ${value}`).join('\n') : 'Keine'
}
</script>
