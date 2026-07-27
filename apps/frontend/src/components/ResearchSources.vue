<template>
  <div v-if="sources.length || research.used" class="research-sources app-surface-tonal">
    <q-expansion-item
      v-if="sources.length"
      icon="language"
      :label="`Verlinkte Webquellen (${sources.length})`"
    >
      <q-list separator>
        <q-item
          v-for="(source, index) in sources"
          :key="`${source.url}-${index}`"
          clickable
          tag="a"
          :href="source.url"
          target="_blank"
          rel="noopener noreferrer"
          class="research-sources__link"
        >
          <q-item-section>{{ source.title }}</q-item-section>
          <q-item-section side>
            <q-icon name="open_in_new" size="16px" aria-hidden="true" />
          </q-item-section>
        </q-item>
      </q-list>
    </q-expansion-item>
    <div v-else class="research-sources__empty text-caption">
      <q-icon name="language" size="16px" aria-hidden="true" />
      <span>Webrecherche verwendet, aber keine Quelle in der Antwort verlinkt.</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { linkedResearchSources } from 'src/utils/research-sources'

defineOptions({ name: 'ResearchSources' })

const props = defineProps({
  research: { type: Object, required: true }
})

const sources = computed(() => linkedResearchSources(props.research))
</script>

<style scoped>
.research-sources {
  max-width: 100%;
  margin-top: 12px;
  overflow: hidden;
  border-radius: 8px;
}

.research-sources__link {
  min-width: 0;
  color: inherit;
  overflow-wrap: anywhere;
}

.research-sources__empty {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  color: var(--app-muted);
}
</style>
