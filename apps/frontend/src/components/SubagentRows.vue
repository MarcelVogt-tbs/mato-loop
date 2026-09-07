<template>
  <q-list v-if="children.length" aria-label="Subagenten">
    <q-item
      v-for="child in children"
      :key="child.id"
      :to="{ name: 'chat', query: { ...route.query, session: child.parentSessionId, subagent: child.id } }"
      class="rounded-borders"
    >
      <q-item-section side aria-hidden="true">
        <q-spinner v-if="!reducedMotion && ['queued', 'running'].includes(child.run?.status)" color="primary" size="20px" />
        <q-icon
          v-else
          :name="indicators[child.run?.status]?.icon || 'schedule'"
          :color="indicators[child.run?.status]?.color || 'primary'"
          size="20px"
        />
      </q-item-section>
      <q-item-section>
        <q-item-label>{{ child.title || 'Subagent' }}</q-item-label>
        <q-item-label caption>{{ subagentStatus(child.run) }}</q-item-label>
      </q-item-section>
      <q-item-section side><q-icon name="chevron_right" /></q-item-section>
    </q-item>
  </q-list>
</template>

<script setup>
import { subagentStatus } from 'stores/subagents'
import { useRoute } from 'vue-router'
const route = useRoute()
const reducedMotion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const indicators = {
  queued: { icon: 'schedule', color: 'primary' },
  running: { icon: 'play_circle_outline', color: 'primary' },
  waiting_approval: { icon: 'pending_actions', color: 'warning' },
  waiting_input: { icon: 'help_outline', color: 'warning' },
  completed: { icon: 'check_circle', color: 'positive' },
  failed: { icon: 'error_outline', color: 'negative' },
  cancelled: { icon: 'cancel', color: 'grey' }
}
defineProps({ children: { type: Array, default: () => [] } })
</script>
