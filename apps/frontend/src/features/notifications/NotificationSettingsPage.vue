<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Benachrichtigungen</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Web Push einmalig für diese MatoLoop-Instanz einrichten.
        </p>
      </div>
      <q-btn
        unelevated
        no-caps
        color="primary"
        icon="save"
        label="Speichern"
        :loading="settings.saving"
        :disable="settings.loading || !settings.loaded"
        @click="save"
      />
    </header>

    <q-banner v-if="settings.loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Benachrichtigungseinstellungen konnten nicht geladen werden.
      <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="load" /></template>
    </q-banner>

    <q-form ref="formRef" @submit.prevent="save">
      <q-card flat class="app-surface">
        <q-card-section class="row items-center q-gutter-md">
          <q-avatar color="primary" text-color="white" icon="notifications_active" />
          <div class="col">
            <div class="row items-center q-gutter-sm">
              <div class="text-subtitle1 text-weight-bold">Web Push</div>
              <q-badge
                :color="settings.configured ? 'positive' : 'grey-7'"
                :label="settings.configured ? 'Eingerichtet' : 'Nicht eingerichtet'"
              />
            </div>
            <div class="text-caption app-muted">
              Ermöglicht Push-Benachrichtigungen in unterstützten Browsern und installierten PWAs.
            </div>
          </div>
          <q-toggle dense v-model="form.enabled" color="primary" label="Aktiv" />
        </q-card-section>
        <q-separator />
        <q-card-section>
          <q-input dense
            v-model.trim="form.contactEmail"
            outlined
            type="email"
            label="Kontakt-E-Mail-Adresse"
            hint="Wird gegenüber den Browser-Pushdiensten als technischer Kontakt angegeben."
            persistent-hint
            maxlength="254"
            :rules="[emailRule]"
          />
          <q-banner rounded class="app-surface-tonal q-mt-md">
            <template #avatar><q-icon name="key" color="primary" /></template>
            Der installationsbezogene Schlüssel wird beim ersten Speichern automatisch erzeugt und anschließend wiederverwendet.
          </q-banner>
        </q-card-section>
        <q-inner-loading :showing="settings.loading" />
      </q-card>
    </q-form>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useWebPushSettingsStore } from 'stores/web-push-settings'

defineOptions({ name: 'NotificationSettingsPage' })

const $q = useQuasar()
const settings = useWebPushSettingsStore()
const formRef = ref(null)
const form = reactive({ enabled: false, contactEmail: '' })
const emailRule = (value) => (
  (!form.enabled && !value)
  || /^[^@\s]+@[^@\s]+$/.test(value)
  || 'Gültige E-Mail-Adresse erforderlich.'
)

function assignForm(data) {
  form.enabled = Boolean(data.enabled)
  form.contactEmail = data.contactEmail || ''
}

async function load() {
  try {
    assignForm(await settings.fetchSettings())
  } catch {
    // The banner and central interceptor display the error.
  }
}

async function save() {
  if (!await formRef.value?.validate()) return
  try {
    assignForm(await settings.saveSettings({
      enabled: form.enabled,
      contactEmail: form.contactEmail || null
    }))
    $q.notify({ type: 'positive', message: 'Benachrichtigungseinstellungen gespeichert.' })
  } catch {
    // The central API interceptor displays the error.
  }
}

onMounted(load)
</script>
