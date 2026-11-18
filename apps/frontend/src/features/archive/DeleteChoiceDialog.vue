<template>
  <q-dialog ref="dialogRef" :transition-duration="0" @hide="onDialogHide">
    <q-card style="width: 480px; max-width: 100vw">
      <q-card-section class="text-h6">{{ label }} löschen?</q-card-section>
      <q-card-section class="q-pt-none">
        <p class="text-break">„{{ title }}“</p>
        <q-checkbox v-if="trashEnabled" v-model="permanent" :disable="permanentLocked" label="Sofort endgültig löschen" />
        <p class="q-mt-md q-mb-none" role="status">{{ permanent
          ? `Unwiderruflich löschen. Eine Wiederherstellung ist nicht möglich.${session ? ' Laufende Aufträge in dieser Session werden beendet.' : ''}`
          : `In den Papierkorb verschieben. Die Wiederherstellung ist ${retentionDays} Tage möglich. Dateien und Automationen bleiben erhalten; neue Aufträge sind bis zur Wiederherstellung gesperrt.${session ? '' : ' Worktrees bleiben erhalten.'}` }}</p>
        <template v-if="session && worktree">
          <q-checkbox v-model="removeWorktree" :disable="Boolean(worktree.removalError)" label="Worktree ebenfalls entfernen" class="q-mt-md" />
          <p v-if="worktree.removalError" class="q-mt-sm q-mb-none" role="status">{{ apiErrorMessage({ error: worktree.removalError }) }}</p>
          <p class="text-break q-mt-sm q-mb-none">Worktree „{{ worktree.path }}“: Das Entfernen ist sofort unwiderruflich, auch wenn die Session im Papierkorb bleibt. Nicht committete Änderungen sowie zusätzliche und ignorierte Dateien wie .env werden unwiderruflich gelöscht. Commits, die durch keinen Branch gesichert sind, können danach verloren gehen. Vorhandene Branches bleiben erhalten.</p>
          <p v-if="worktree.sessions?.length" class="q-mt-sm q-mb-none">{{ worktree.sessions.length }} weitere Session{{ worktree.sessions.length === 1 ? '' : 's' }} wechseln beim Entfernen zu Lokal: {{ worktree.sessions.map(item => item.title || 'Ohne Titel').join(', ') }}.</p>
        </template>
        <p v-else-if="session && hasWorktree" class="q-mt-md q-mb-none" role="status">Der Worktree kann hier derzeit nicht geprüft oder entfernt werden und bleibt erhalten.</p>
      </q-card-section>
      <q-card-actions align="right">
        <q-btn flat no-caps label="Abbrechen" @click="onDialogCancel" />
        <q-btn unelevated no-caps color="negative" :label="permanent ? (session ? 'Endgültig löschen' : 'Weiter zum endgültigen Löschen') : 'In Papierkorb verschieben'" @click="submit" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { useDialogPluginComponent } from 'quasar'
import { apiErrorMessage } from 'src/utils/api-errors'

const props = defineProps({
  label: { type: String, required: true }, title: { type: String, required: true },
  retentionDays: { type: Number, required: true }, session: Boolean,
  trashEnabled: { type: Boolean, default: true }, permanentLocked: Boolean,
  worktree: { type: Object, default: null }, hasWorktree: Boolean
})
defineEmits([...useDialogPluginComponent.emits])
const { dialogRef, onDialogHide, onDialogOK, onDialogCancel } = useDialogPluginComponent()
const permanent = ref(props.permanentLocked)
const removeWorktree = ref(false)

function submit() {
  onDialogOK(props.session ? {
    permanent: permanent.value,
    worktreeImpact: removeWorktree.value && !props.worktree?.removalError ? props.worktree?.impact || null : null
  } : permanent.value)
}
</script>
