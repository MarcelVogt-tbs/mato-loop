<template>
  <div class="q-py-sm" :style="{ colorScheme: $q.dark.isActive ? 'dark' : 'light' }">
    <div class="row items-center justify-between q-mb-sm">
      <div class="text-body2">{{ scheduleSummary(schedule) }}</div>
      <q-btn v-if="removable" :disable="readonly" flat round dense icon="delete_outline" color="negative" :aria-label="`Zeitplan ${index + 1} entfernen`" @click="$emit('remove')" />
    </div>
    <div class="row q-col-gutter-sm">
      <div v-if="multiple" class="col-12 col-sm-4">
        <q-select v-model="schedule.kind" :readonly="readonly" outlined dense emit-value map-options label="Art des Zeitplans" :options="[{ label: 'Einmalig', value: 'once' }, { label: 'Wiederkehrend', value: 'cron' }]" />
      </div>
      <template v-if="schedule.kind === 'cron'">
        <div class="col-12 col-sm-6">
          <q-select v-model="schedule.preset" :readonly="readonly" outlined dense emit-value map-options label="Wiederholung" :options="schedulePresets" @update:model-value="applySchedulePreset(schedule)" />
        </div>
        <div v-if="schedule.preset !== 'custom'" class="col-12 col-sm-6">
          <q-input v-model="schedule.time" :readonly="readonly" outlined dense type="time" stack-label label="Uhrzeit" @update:model-value="applySchedulePreset(schedule)" />
        </div>
        <div v-if="schedule.preset === 'weekly'" class="col-12 col-sm-6">
          <q-select v-model="schedule.weekday" :readonly="readonly" outlined dense emit-value map-options label="Wochentag" :options="weekdays" @update:model-value="applySchedulePreset(schedule)" />
        </div>
        <div v-if="schedule.preset === 'monthly'" class="col-12 col-sm-6">
          <q-input v-model.number="schedule.monthDay" :readonly="readonly" outlined dense type="number" min="1" max="31" label="Tag im Monat" @update:model-value="applySchedulePreset(schedule)" />
          <p class="text-caption app-muted q-mt-xs q-mb-none">In Monaten ohne diesen Tag entfällt der Termin.</p>
        </div>
        <div v-if="schedule.preset === 'custom'" class="col-12">
          <q-input v-model="schedule.cron" :readonly="readonly" outlined dense label="Cron-Ausdruck" maxlength="255" />
          <p class="text-caption app-muted q-mt-xs q-mb-none">Minute Stunde Tag Monat Wochentag; die genaue Prüfung erfolgt beim Speichern.</p>
        </div>
      </template>
      <div v-else class="col-12 col-sm-8">
        <q-input v-model="schedule.runAt" :readonly="readonly" outlined dense stack-label type="datetime-local" step="any" label="Ausführungszeitpunkt" />
        <p class="text-caption app-muted q-mt-xs q-mb-none">Lokale Uhrzeit dieses Geräts ({{ browserTimezone() }})</p>
      </div>
    </div>
    <q-expansion-item v-model="schedule.advanced" dense label="Zeitplanoptionen" :caption="optionsSummary" class="q-mt-sm">
      <div class="q-pa-sm q-gutter-md">
        <template v-if="schedule.kind === 'cron'">
          <q-input v-model="schedule.timezone" :readonly="readonly" outlined dense label="Zeitzone" maxlength="100" />
          <p class="text-caption app-muted">IANA-Zeitzone, zum Beispiel Europe/Berlin</p>
          <q-input v-model="schedule.validUntil" :readonly="readonly" outlined dense stack-label type="datetime-local" step="any" label="Gültig bis (optional)" />
          <p class="text-caption app-muted">Letzter Termin inklusive; lokale Uhrzeit dieses Geräts ({{ browserTimezone() }})</p>
        </template>
        <q-toggle v-model="schedule.enabled" :disable="readonly" dense label="Diesen Zeitplan aktivieren" />
      </div>
    </q-expansion-item>
    <div v-if="error" role="alert" class="text-negative text-caption q-mt-sm">{{ error }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { applySchedulePreset, browserTimezone, schedulePresets, scheduleSummary, weekdays } from 'src/utils/automation-schedules'
import { formatAbsoluteDateTime } from 'src/utils/date-time'

const schedule = defineModel({ type: Object, required: true })
defineProps({ index: { type: Number, default: 0 }, multiple: Boolean, removable: Boolean, readonly: Boolean, error: { type: String, default: '' } })
defineEmits(['remove'])
const optionsSummary = computed(() => [
  schedule.value.kind === 'cron' ? schedule.value.timezone : '',
  schedule.value.kind === 'cron' && schedule.value.validUntil ? `Bis ${formatAbsoluteDateTime(schedule.value.validUntil)}` : '',
  !schedule.value.enabled ? 'Zeitplan deaktiviert' : ''
].filter(Boolean).join(' · '))
</script>
