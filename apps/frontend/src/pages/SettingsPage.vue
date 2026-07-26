<template>
  <q-page class="app-page settings-page column no-wrap">
    <AppPageHeader title="Einstellungen" subtitle="Persona, Provider, Standardmodelle, Integrationen, Tools und Memory zentral verwalten." />

    <q-tabs
      no-caps
      align="left"
      active-color="primary"
      indicator-color="primary"
      class="settings-category-tabs app-surface q-mb-md"
      mobile-arrows
      outside-arrows
    >
      <q-route-tab icon="dns" label="Provider" :to="{ name: 'settings-providers' }" />
      <q-route-tab icon="stars" label="Standardmodelle" :to="{ name: 'settings-default-models' }" />
      <q-route-tab icon="extension" label="Integrationen" :to="{ name: 'settings-integrations' }" />
      <q-route-tab icon="build" label="Tools" :to="{ name: 'settings-tools' }" />
      <q-route-tab icon="face" label="Persona" :to="{ name: 'settings-persona' }" />
      <q-route-tab icon="psychology" label="Memory" :to="{ name: 'settings-memory' }" />
    </q-tabs>

    <div class="settings-layout row no-wrap items-stretch col">
      <nav class="settings-category-nav q-py-xs q-pr-md" aria-label="Einstellungskategorien">
        <q-list class="settings-category-list">
          <q-item
            clickable
            v-ripple
            class="q-mb-xs"
            :to="{ name: 'settings-providers' }"
            active-class="app-nav-item--active"
          >
            <q-item-section avatar><q-icon name="dns" /></q-item-section>
            <q-item-section>Provider</q-item-section>
          </q-item>
          <q-item
            clickable
            v-ripple
            class="q-mb-xs"
            :to="{ name: 'settings-default-models' }"
            active-class="app-nav-item--active"
          >
            <q-item-section avatar><q-icon name="stars" /></q-item-section>
            <q-item-section>Standardmodelle</q-item-section>
          </q-item>
          <q-item
            clickable
            v-ripple
            class="q-mb-xs"
            :to="{ name: 'settings-integrations' }"
            active-class="app-nav-item--active"
          >
            <q-item-section avatar><q-icon name="extension" /></q-item-section>
            <q-item-section>Integrationen</q-item-section>
          </q-item>
          <q-item
            clickable
            v-ripple
            class="q-mb-xs"
            :to="{ name: 'settings-tools' }"
            active-class="app-nav-item--active"
          >
            <q-item-section avatar><q-icon name="build" /></q-item-section>
            <q-item-section>Tools</q-item-section>
          </q-item>
          <q-item
            clickable
            v-ripple
            class="q-mb-xs"
            :to="{ name: 'settings-persona' }"
            active-class="app-nav-item--active"
          >
            <q-item-section avatar><q-icon name="face" /></q-item-section>
            <q-item-section>Persona</q-item-section>
          </q-item>
          <q-item
            clickable
            v-ripple
            class="q-mb-xs"
            :to="{ name: 'settings-memory' }"
            active-class="app-nav-item--active"
          >
            <q-item-section avatar><q-icon name="psychology" /></q-item-section>
            <q-item-section>Memory</q-item-section>
          </q-item>
        </q-list>
      </nav>

      <q-separator vertical class="settings-category-separator q-mr-lg" />

      <main class="settings-category-content col q-pr-sm">
        <router-view />
      </main>
    </div>
  </q-page>
</template>

<script setup>
import AppPageHeader from 'components/AppPageHeader.vue'

defineOptions({ name: 'SettingsPage' })
</script>
