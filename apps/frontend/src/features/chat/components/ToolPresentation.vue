<template>
  <div v-if="items.length" class="tool-presentation">
    <dl class="tool-presentation__fields q-ma-none">
      <div
        v-for="(item, index) in items"
        :key="`${item.label}:${index}`"
        :class="{ 'tool-presentation__code': item.format === 'code' }"
      >
        <dt class="text-caption app-muted">{{ kind === 'shell' && index === 0 ? 'Befehl' : item.label }}</dt>
        <dd class="q-ma-none">
          <pre v-if="item.format === 'code'" class="tool-call__details" :aria-label="item.label">{{ item.value }}</pre>
          <ul v-else-if="item.format === 'list' && item.value.length" class="text-body2 q-my-none q-pl-md">
            <li v-for="(value, valueIndex) in item.value" :key="valueIndex">{{ value }}</li>
          </ul>
          <div v-else class="tool-presentation__text text-body2">{{ item.format === 'list' ? 'Keine' : item.value }}</div>
        </dd>
      </div>
    </dl>
  </div>
</template>

<script setup>
defineOptions({ name: 'ToolPresentation' })

defineProps({
  items: { type: Array, required: true },
  kind: { type: String, default: null }
})
</script>

<style scoped>
.tool-presentation {
  container-type: inline-size;
}

.tool-presentation__fields {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 8px 16px;
}

.tool-presentation__code {
  grid-column: 1 / -1;
}

.tool-presentation__text {
  white-space: pre-wrap;
}

@container (min-width: 600px) {
  .tool-presentation__fields {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
