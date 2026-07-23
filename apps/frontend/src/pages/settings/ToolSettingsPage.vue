<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Tools</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Netzwerkzugriffe des Agents global steuern und private Ziele gezielt freigeben.
        </p>
      </div>
      <q-btn
        color="primary"
        unelevated
        no-caps
        icon="save"
        label="Speichern"
        :loading="tools.saving"
        :disable="tools.loading || !tools.loaded"
        @click="save"
      />
    </header>

    <q-form ref="formRef" @submit.prevent="save">
      <q-banner v-if="tools.loadFailed" rounded class="bg-negative text-white q-mb-md">
        <template #avatar><q-icon name="error_outline" /></template>
        Die Tool-Einstellungen konnten nicht geladen werden.
        <template #action>
          <q-btn flat no-caps color="white" label="Erneut laden" @click="load" />
        </template>
      </q-banner>
      <q-card flat class="app-surface tool-settings-card">
        <q-card-section class="row items-start no-wrap q-gutter-md">
          <q-avatar color="primary" text-color="white" icon="language" />
          <div class="col">
            <div class="text-subtitle1 text-weight-bold">Webfetch</div>
            <div class="text-body2 app-muted">
              Ruft HTTP(S)-Inhalte ab. Weiterleitungen und Zieladressen werden serverseitig geprüft.
            </div>
          </div>
          <q-toggle v-model="form.enabled" color="primary" label="Global aktiv" />
        </q-card-section>

        <q-separator />

        <q-card-section class="q-gutter-md">
          <q-banner v-if="!form.enabled" rounded class="app-surface-tonal">
            <template #avatar><q-icon name="block" color="warning" /></template>
            Webfetch ist für alle Modelle deaktiviert. Modell-Overrides bleiben gespeichert, werden aber nicht wirksam.
          </q-banner>

          <q-select
            v-model="form.defaultPolicy"
            outlined
            emit-value
            map-options
            label="Globale Standardpolicy"
            :options="WEBFETCH_DEFAULT_POLICY_OPTIONS"
            hint="Modelle mit „Globalen Standard übernehmen“ verwenden diese Policy."
          />

          <q-banner v-if="form.defaultPolicy === 'auto_allowed'" rounded class="app-surface-tonal">
            <template #avatar><q-icon name="warning_amber" color="warning" /></template>
            Erlaubte private Origins können ohne einzelne Rückfrage abgerufen werden. Trage nur vertrauenswürdige interne Dienste ein.
          </q-banner>

          <div>
            <div class="text-subtitle2">Erlaubte private Origins</div>
            <div class="text-caption app-muted q-mb-sm">
              Nur vollständige Origins wie <code>http://127.0.0.1:8080</code>, ohne Pfad oder Zugangsdaten.
              Metadaten-, Link-Local- und andere gefährliche Adressen bleiben blockiert.
            </div>

            <div v-for="(_, index) in form.allowedPrivateOrigins" :key="index" class="tool-origin-row q-mb-sm">
              <q-input
                v-model="form.allowedPrivateOrigins[index]"
                outlined
                dense
                :label="`Private Origin ${index + 1}`"
                :rules="[originRule]"
                lazy-rules
              >
                <template #prepend><q-icon name="lan" /></template>
              </q-input>
              <q-btn
                flat
                round
                dense
                color="negative"
                icon="delete_outline"
                :aria-label="`Private Origin ${index + 1} entfernen`"
                @click="form.allowedPrivateOrigins.splice(index, 1)"
              />
            </div>

            <q-btn
              outline
              no-caps
              color="primary"
              icon="add"
              label="Origin hinzufügen"
              @click="form.allowedPrivateOrigins.push('')"
            />
          </div>
        </q-card-section>
        <q-inner-loading :showing="tools.loading" />
      </q-card>
    </q-form>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useToolSettingsStore } from 'stores/tool-settings'
import { WEBFETCH_DEFAULT_POLICY_OPTIONS, normalizePrivateOrigin } from 'src/utils/webfetch'

defineOptions({ name: 'ToolSettingsPage' })

const $q = useQuasar()
const tools = useToolSettingsStore()
const formRef = ref(null)
const form = reactive({ enabled: true, defaultPolicy: 'ask', allowedPrivateOrigins: [] })

function assignForm(settings) {
  Object.assign(form, settings, { allowedPrivateOrigins: [...settings.allowedPrivateOrigins] })
}

function originRule(value) {
  const result = normalizePrivateOrigin(value)
  return result.valid || result.message
}

async function save() {
  if (!tools.loaded || !await formRef.value?.validate()) return
  const allowedPrivateOrigins = [...new Set(
    form.allowedPrivateOrigins.map((origin) => normalizePrivateOrigin(origin).value)
  )]
  const saved = await tools.saveSettings({
    enabled: form.enabled,
    defaultPolicy: form.defaultPolicy,
    allowedPrivateOrigins
  })
  assignForm(saved)
  $q.notify({ type: 'positive', message: 'Tool-Einstellungen gespeichert' })
}

async function load() {
  try {
    assignForm(await tools.fetchSettings())
  } catch {
    // Der zentrale Axios-Interceptor zeigt die Fehlermeldung an.
  }
}

onMounted(load)
</script>
