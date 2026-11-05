<template>
  <q-list aria-label="Agentenaufträge">
    <q-item v-if="catalog.loading && !catalog.items.length" dense><q-item-section>Wird geladen …</q-item-section></q-item>
    <q-item v-if="catalog.failed" dense clickable @click="$emit('refresh')"><q-item-section>Befehle erneut laden</q-item-section></q-item>
    <template v-for="(group, index) in groups" :key="group.label">
      <q-separator v-if="index" />
      <q-item-label header class="q-py-sm" role="heading" aria-level="3">{{ group.label }}</q-item-label>
      <q-item v-for="command in group.items" :key="command.name" dense clickable :disable="busy" @click="run(command)">
        <q-item-section avatar><q-icon :name="command.icon" color="primary" /></q-item-section>
        <q-item-section>
          <q-item-label>{{ command.name === 'git-merge' && targetBranch ? `Merge in „${targetBranch}“` : command.label }}</q-item-label>
          <q-item-label caption>{{ command.description }}</q-item-label>
        </q-item-section>
        <q-item-section side><q-icon name="play_arrow" /></q-item-section>
      </q-item>
    </template>
  </q-list>
</template>

<script setup>
import { computed } from 'vue'
import { useCommandStore } from 'src/features/commands/store'
import { useGitActionStore } from './git-actions-store'

const props = defineProps({ disabled: Boolean, targetBranch: { type: String, default: null } })
defineEmits(['refresh'])
const catalog = useCommandStore(), actions = useGitActionStore()
const items = computed(() => catalog.items.filter(command => command.source === 'git'))
const groups = computed(() => [
  { label: 'Prüfen & sichern', names: ['git-review', 'git-commit'] },
  { label: 'Übernehmen', names: ['git-merge', 'git-finish', 'git-pr'] },
  { label: 'Remote', names: ['git-push', 'git-sync'] }
].map(group => ({ ...group, items: group.names.map(name => items.value.find(command => command.name === name)).filter(Boolean) }))
  .filter(group => group.items.length))
const busy = computed(() => props.disabled || (catalog.loading && !catalog.loaded) || actions.messageDisabled)
async function run(command) {
  if (busy.value) return
  try { await actions.run(command.name) } catch { /* Central API toast. */ }
}
</script>
