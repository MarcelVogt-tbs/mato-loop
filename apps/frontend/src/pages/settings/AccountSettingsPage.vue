<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Konto</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Persönliche Anmeldedaten für {{ auth.username }} verwalten.
        </p>
      </div>
    </header>

    <q-form @submit.prevent="changePassword">
      <q-card flat class="app-surface">
        <q-card-section class="row items-center no-wrap q-gutter-md">
          <q-avatar color="primary" text-color="white" icon="manage_accounts" />
          <div>
            <div class="text-subtitle1 text-weight-bold">{{ auth.username }}</div>
            <div class="text-body2 app-muted">{{ auth.isAdmin ? 'Administrator' : 'Benutzer' }}</div>
          </div>
        </q-card-section>
        <q-separator />
        <q-card-section class="q-gutter-md">
          <div class="text-subtitle1 text-weight-medium">Passwort ändern</div>
          <q-input
            v-model="currentPassword"
            outlined
            type="password"
            label="Aktuelles Passwort"
            autocomplete="current-password"
            :rules="[requiredRule]"
          />
          <q-input
            v-model="newPassword"
            outlined
            type="password"
            label="Neues Passwort"
            autocomplete="new-password"
            hint="Mindestens 10 Zeichen"
            :rules="[passwordRule]"
          />
          <q-input
            v-model="passwordConfirmation"
            outlined
            type="password"
            label="Neues Passwort bestätigen"
            autocomplete="new-password"
            :rules="[confirmationRule]"
          />
          <q-banner rounded class="app-surface-tonal">
            Nach der Änderung musst du dich mit dem neuen Passwort erneut anmelden.
          </q-banner>
        </q-card-section>
        <q-card-actions align="right" class="q-pa-md q-pt-none">
          <q-btn
            unelevated
            no-caps
            color="primary"
            type="submit"
            icon="password"
            label="Passwort ändern"
            :loading="saving"
          />
        </q-card-actions>
      </q-card>
    </q-form>
  </section>
</template>

<script setup>
import { ref } from 'vue'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'AccountSettingsPage' })

const auth = useAuthStore()
const currentPassword = ref('')
const newPassword = ref('')
const passwordConfirmation = ref('')
const saving = ref(false)
const requiredRule = (value) => Boolean(value) || 'Pflichtfeld'
const passwordRule = (value) => String(value || '').length >= 10 || 'Das Passwort muss mindestens 10 Zeichen lang sein.'
const confirmationRule = (value) => value === newPassword.value || 'Die Passwörter stimmen nicht überein.'

async function changePassword() {
  saving.value = true
  try {
    await auth.changePassword({
      current_password: currentPassword.value,
      new_password: newPassword.value
    })
  } catch {
    // The central API interceptor displays the error message.
  } finally {
    saving.value = false
  }
}
</script>
