<template>
  <q-list v-if="items.length" dense bordered separator class="rounded-borders">
    <q-item v-for="(item, index) in items" :key="`${item.label}:${index}`">
      <q-item-section>
        <q-item-label caption>{{ item.label }}</q-item-label>
        <pre v-if="item.format !== 'text'" class="tool-call__details q-mt-xs">{{ itemText(item) }}</pre>
        <q-item-label v-else class="text-body2">{{ item.value }}</q-item-label>
      </q-item-section>
    </q-item>
  </q-list>
</template>

<script setup>
defineOptions({ name: 'ToolPresentation' })

defineProps({
  items: { type: Array, required: true }
})

function itemText(item) {
  if (!Array.isArray(item.value)) return item.value
  return item.value.length ? item.value.map((value) => `- ${value}`).join('\n') : 'Keine'
}
</script>
