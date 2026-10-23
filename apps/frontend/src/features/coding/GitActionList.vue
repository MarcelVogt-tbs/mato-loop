<template>
  <q-list separator aria-label="Agentenaufträge">
    <q-item v-if="catalog.loading && !catalog.items.length"><q-item-section>Wird geladen …</q-item-section></q-item>
    <q-item v-if="catalog.failed" clickable @click="$emit('refresh')"><q-item-section>Befehle erneut laden</q-item-section></q-item>
    <q-item v-for="command in items" :key="command.name" clickable :disable="busy || Boolean(command.disabledReason)" @click="run(command)">
      <q-item-section avatar><q-icon :name="command.icon" color="primary" /></q-item-section>
      <q-item-section>
        <q-item-label>{{ command.label }}</q-item-label>
        <q-item-label caption>{{ command.disabledReason || command.description }}</q-item-label>
      </q-item-section>
      <q-item-section side><q-icon name="play_arrow" /></q-item-section>
    </q-item>
  </q-list>
</template>

<script setup>
import { computed } from 'vue'
import { useCommandStore } from 'src/features/commands/store'
import { useGitActionStore } from './git-actions-store'

const props = defineProps({ disabled: Boolean })
defineEmits(['refresh'])
const catalog = useCommandStore(), actions = useGitActionStore()
const items = computed(() => catalog.items.filter(command => command.source === 'git' && command.name !== 'git-remote'))
const busy = computed(() => props.disabled || catalog.loading || actions.busy)
async function run(command) {
  if (busy.value || command.disabledReason) return
  try { await actions.run(command.name) } catch { /* Central API toast. */ }
}
</script>
