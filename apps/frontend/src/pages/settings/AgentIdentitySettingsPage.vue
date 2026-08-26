<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Persona des Assistenten</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Name, Verhalten und Charakter des gemeinsamen Assistenten festlegen.
        </p>
      </div>
      <q-btn
        color="primary"
        unelevated
        no-caps
        icon="save"
        label="Speichern"
        :loading="identity.saving"
        :disable="identity.loading || !identity.loaded"
        @click="save"
      />
    </header>

    <q-banner v-if="identity.loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Persona konnte nicht geladen werden.
      <template #action>
        <q-btn flat no-caps color="white" label="Erneut laden" @click="load" />
      </template>
    </q-banner>

    <q-form ref="formRef" @submit.prevent="save">
      <q-card flat class="app-surface">
        <q-card-section class="row items-start no-wrap q-gutter-md">
          <q-avatar color="primary" text-color="white" icon="face" />
          <div class="col">
            <div class="text-subtitle1 text-weight-bold">Gemeinsame Identität</div>
            <div class="text-body2 app-muted">
              Diese Persona gilt für alle Sessions und Agent-Modi. Änderungen über den Chat
              verwenden die unter Tools konfigurierte Ausführungsrichtlinie.
            </div>
          </div>
        </q-card-section>
        <q-separator />
        <q-card-section class="q-gutter-md">
          <q-input
            v-model="form.displayName"
            outlined
            label="Agent-Name"
            maxlength="80"
            counter
            :rules="[requiredRule]"
            lazy-rules
            hint="Dieser Name erscheint als Sprecher im Chat."
          >
            <template #prepend><q-icon name="badge" /></template>
          </q-input>
          <q-input
            v-model="form.instructions"
            outlined
            autogrow
            type="textarea"
            rows="10"
            maxlength="16000"
            counter
            label="Verhalten und Charakter"
            hint="Beschreibe Ton, Persönlichkeit, Arbeitsweise und allgemeine Präferenzen."
          />
          <q-banner rounded class="app-surface-tonal">
            <template #avatar><q-icon name="privacy_tip" color="primary" /></template>
            Die Persona wird bei normalen Agent-Anfragen an das ausgewählte Modell übertragen.
            Speichere hier keine Passwörter, API-Keys oder anderen Secrets.
          </q-banner>
        </q-card-section>
        <q-inner-loading :showing="identity.loading" />
      </q-card>
    </q-form>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useAgentIdentityStore } from 'stores/agent-identity'

defineOptions({ name: 'AgentIdentitySettingsPage' })

const $q = useQuasar()
const identity = useAgentIdentityStore()
const formRef = ref(null)
const form = reactive({ displayName: 'MatoLoop', instructions: '' })

function assignForm(value) {
  form.displayName = value.displayName
  form.instructions = value.instructions
}

function requiredRule(value) {
  return Boolean(String(value || '').trim()) || 'Pflichtfeld'
}

async function load() {
  try {
    assignForm(await identity.fetchIdentity())
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

async function save() {
  if (!identity.loaded || !await formRef.value?.validate()) return
  try {
    assignForm(await identity.saveIdentity({
      displayName: form.displayName.trim(),
      instructions: form.instructions.trim()
    }))
    $q.notify({ type: 'positive', message: 'Persona gespeichert' })
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

onMounted(load)
</script>
