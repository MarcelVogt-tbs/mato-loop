<template>
  <q-item
    v-if="request.payload?.subagentSessionId"
    :to="{ name: 'chat', query: { ...(route.query.session === request.sessionId ? route.query : {}), session: request.sessionId, subagent: request.payload.subagentSessionId } }"
    class="rounded-borders"
  >
    <q-item-section>
      <q-item-label caption>Anfrage des Subagenten</q-item-label>
      <q-item-label>{{ request.payload.subagentTitle || 'Subagent' }}</q-item-label>
    </q-item-section>
    <q-item-section side><q-icon name="chevron_right" /></q-item-section>
  </q-item>
</template>

<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()
defineProps({ request: { type: Object, required: true } })
</script>
