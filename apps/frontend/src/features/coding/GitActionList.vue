<template>
  <q-list aria-label="Agentenaufträge">
    <q-item v-if="catalog.loading && !catalog.items.length" dense><q-item-section>Wird geladen …</q-item-section></q-item>
    <q-item v-if="catalog.failed" dense clickable @click="$emit('refresh')"><q-item-section>Befehle erneut laden</q-item-section></q-item>
    <q-item v-for="command in primary" :key="command.name" dense clickable :disable="busy" @click="run(command)">
      <q-item-section avatar><q-icon :name="command.icon" color="primary" /></q-item-section>
      <q-item-section>
        <q-item-label>{{ command.label }}</q-item-label>
        <q-item-label caption>{{ command.description }}</q-item-label>
      </q-item-section>
      <q-item-section side><q-icon name="play_arrow" /></q-item-section>
    </q-item>
    <q-expansion-item v-if="secondary.length" dense label="Weitere Git-Aktionen">
      <q-item v-for="command in secondary" :key="command.name" dense clickable :disable="busy" @click="run(command)">
        <q-item-section avatar><q-icon :name="command.icon" color="primary" /></q-item-section>
        <q-item-section>
          <q-item-label>{{ command.label }}</q-item-label>
          <q-item-label caption>{{ command.description }}</q-item-label>
        </q-item-section>
        <q-item-section side><q-icon name="play_arrow" /></q-item-section>
      </q-item>
    </q-expansion-item>
  </q-list>
</template>

<script setup>
import { computed } from 'vue'
import { useCommandStore } from 'src/features/commands/store'
import { useGitActionStore } from './git-actions-store'

const props = defineProps({ disabled: Boolean })
defineEmits(['refresh'])
const catalog = useCommandStore(), actions = useGitActionStore()
const items = computed(() => catalog.items.filter(command => command.source === 'git'))
const commands = names => names.map(name => items.value.find(command => command.name === name)).filter(Boolean)
const primary = computed(() => commands(['git-review', 'git-finish', 'git-push', 'git-pr']))
const secondary = computed(() => commands(['git-commit', 'git-merge', 'git-sync']))
const busy = computed(() => props.disabled || (catalog.loading && !catalog.loaded) || actions.messageDisabled)
async function run(command) {
  if (busy.value) return
  try { await actions.run(command.name) } catch { /* Central API toast. */ }
}
</script>
