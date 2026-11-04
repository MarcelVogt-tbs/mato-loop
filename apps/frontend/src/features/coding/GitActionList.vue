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
      <q-item v-if="group.commit" tag="label" dense :disable="busy">
        <q-item-section avatar>
          <q-checkbox v-model="commitFirst" dense :disable="busy" aria-label="Vor Merge oder Pull Request committen" />
        </q-item-section>
        <q-item-section><q-item-label class="text-caption">Vor Merge oder Pull Request committen</q-item-label></q-item-section>
      </q-item>
    </template>
    <template v-if="actions.finishEntry || (autoMerge && items.some(command => command.name === 'git-commit'))">
      <q-separator />
      <q-item tag="label" :disable="finishDisabled">
        <q-item-section avatar>
          <q-checkbox :model-value="Boolean(actions.finishEntry)" dense :disable="finishDisabled" aria-label="Nach Umsetzung abschließen" @update:model-value="setFinishQueued" />
        </q-item-section>
        <q-item-section>
          <q-item-label>Nach Umsetzung abschließen</q-item-label>
          <q-item-label caption>{{ actions.finishEntry ? '/git-finish ist eingeplant. Entferne den Haken, um die Nachricht zu entfernen.' : `Review → Korrektur bei Bedarf → Commit → Merge in „${targetBranch || 'Zielbranch'}“. Plant /git-finish als normale Nachricht ein. Ohne laufende Antwort startet der Auftrag sofort.` }}</q-item-label>
        </q-item-section>
      </q-item>
    </template>
  </q-list>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useChatStore } from 'src/features/chat/store'
import { useCommandStore } from 'src/features/commands/store'
import { useGitActionStore } from './git-actions-store'

const props = defineProps({ disabled: Boolean, targetBranch: { type: String, default: null }, autoMerge: Boolean })
defineEmits(['refresh'])
const catalog = useCommandStore(), actions = useGitActionStore()
const chat = useChatStore(), commitFirst = ref(false)
const items = computed(() => catalog.items.filter(command => command.source === 'git' && command.name !== 'git-remote'))
const groups = computed(() => [
  { label: 'Repository einrichten', names: ['git-init'] },
  { label: 'Prüfen & sichern', names: ['git-review', 'git-commit', 'git-finish'] },
  { label: 'Übernehmen & veröffentlichen', names: ['git-merge', 'git-pr'], commit: true },
  { label: 'Remote', names: ['git-push', 'git-sync'] }
].map(group => ({ ...group, items: group.names.map(name => items.value.find(command => command.name === name)).filter(Boolean) }))
  .filter(group => group.items.length))
const busy = computed(() => props.disabled || (catalog.loading && !catalog.loaded) || actions.messageDisabled)
const finishDisabled = computed(() => actions.finishEntry
  ? actions.messageDisabled || actions.finishEntry.id === chat.queueSendingEntryId
  : busy.value)
watch(() => chat.current?.id, () => { commitFirst.value = false })
async function setFinishQueued(enabled) {
  try { await actions.setFinishQueued(enabled) } catch { /* Central API toast. */ }
}
async function run(command) {
  if (busy.value) return
  try { await actions.run(command.name, [], commitFirst.value && ['git-merge', 'git-pr'].includes(command.name)) } catch { /* Central API toast. */ }
}
</script>
