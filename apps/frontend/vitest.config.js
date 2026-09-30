import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      ...Object.fromEntries(['src', 'components', 'stores'].map((name) => [
        name, fileURLToPath(new URL(name === 'src' ? './src' : `./src/${name}`, import.meta.url))
      ])),
      '#q-app/wrappers': 'quasar/wrappers'
    }
  }
})
