<template>
  <q-dialog ref="dialogRef" :transition-duration="0" @hide="onDialogHide">
    <q-card style="width: 480px; max-width: 100vw">
      <q-card-section class="text-h6">{{ label }} löschen?</q-card-section>
      <q-card-section class="q-pt-none">
        <p class="text-break">„{{ title }}“</p>
        <q-checkbox v-model="permanent" label="Sofort endgültig löschen" />
        <p class="q-mt-md q-mb-none" role="status">{{ permanent
          ? 'Unwiderruflich löschen. Eine Wiederherstellung ist nicht möglich.'
          : `In den Papierkorb verschieben. Die Wiederherstellung ist ${retentionDays} Tage möglich. Dateien, Worktrees und Automationen bleiben erhalten; neue Aufträge sind bis zur Wiederherstellung gesperrt.` }}</p>
      </q-card-section>
      <q-card-actions align="right">
        <q-btn flat no-caps label="Abbrechen" @click="onDialogCancel" />
        <q-btn unelevated no-caps color="negative" :label="permanent ? 'Weiter zum endgültigen Löschen' : 'In Papierkorb verschieben'" @click="onDialogOK(permanent)" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { useDialogPluginComponent } from 'quasar'

defineProps({ label: { type: String, required: true }, title: { type: String, required: true }, retentionDays: { type: Number, required: true } })
defineEmits([...useDialogPluginComponent.emits])
const { dialogRef, onDialogHide, onDialogOK, onDialogCancel } = useDialogPluginComponent()
const permanent = ref(false)
</script>
