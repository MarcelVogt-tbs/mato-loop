<template>
  <q-layout view="hHh lpR fFf" class="auth-shell">
    <q-page-container>
      <q-page class="column flex-center q-pa-lg">
        <q-card flat class="app-surface auth-card">
          <q-card-section class="text-center q-pb-none">
            <AppLogo :size="64" />
            <h1 class="text-h5 text-weight-bold q-mb-xs">MatoLoop</h1>
            <p class="app-muted q-mt-none">
              {{ auth.setupRequired ? 'Richte deinen lokalen Benutzer ein.' : 'Melde dich in deinem Workspace an.' }}
            </p>
          </q-card-section>
          <q-form @submit.prevent="submit">
            <q-card-section class="q-gutter-md">
              <q-input v-model="username" outlined label="Benutzername" autocomplete="username" autofocus />
              <q-input
                v-model="password"
                outlined
                type="password"
                label="Passwort"
                :hint="auth.setupRequired ? 'Mindestens 10 Zeichen' : undefined"
                :autocomplete="auth.setupRequired ? 'new-password' : 'current-password'"
              />
            </q-card-section>
            <q-card-actions align="right" class="q-pa-md q-pt-none">
              <q-btn
                type="submit"
                color="primary"
                unelevated
                no-caps
                :loading="loading"
                :label="auth.setupRequired ? 'MatoLoop einrichten' : 'Anmelden'"
              />
            </q-card-actions>
          </q-form>
        </q-card>
      </q-page>
    </q-page-container>
  </q-layout>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLogo from 'src/shared/ui/AppLogo.vue'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'AuthPage' })

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)

async function submit() {
  loading.value = true
  try {
    const credentials = { username: username.value, password: password.value }
    if (auth.setupRequired) await auth.setup(credentials)
    else await auth.login(credentials)
    await router.replace(route.query.redirect || '/')
  } catch {
    // The central API interceptor displays the error as a toast.
  } finally {
    loading.value = false
  }
}
</script>
