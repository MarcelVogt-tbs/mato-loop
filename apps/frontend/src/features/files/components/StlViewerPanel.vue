<template>
  <section class="stl-viewer relative-position fit" role="region" aria-label="Interaktive 3D-Modellansicht">
    <div ref="canvasHost" class="stl-viewer__canvas fit" />

    <div v-if="loading" class="stl-viewer__state fit flex flex-center" aria-live="polite">
      <div class="column items-center q-gutter-sm">
        <q-spinner color="primary" size="36px" />
        <span>3D-Modell wird geladen …</span>
      </div>
    </div>
    <AppStateMessage
      v-else-if="errorMessage"
      class="stl-viewer__state fit q-pa-lg"
      role="alert"
      icon="view_in_ar"
      title="3D-Modell konnte nicht dargestellt werden"
      :description="errorMessage"
    >
      <template #actions>
        <q-btn color="primary" no-caps label="Erneut versuchen" @click="load" />
      </template>
    </AppStateMessage>

    <div v-show="!loading && !errorMessage" class="stl-viewer__controls app-surface row items-center justify-start no-wrap">
      <q-btn flat no-caps icon="restart_alt" label="Zurücksetzen" aria-label="3D-Ansicht zurücksetzen" @click="setView('iso')" />
      <q-separator vertical />
      <q-btn flat icon="zoom_in" aria-label="3D-Ansicht vergrößern" @click="zoom(0.8)" />
      <q-btn flat icon="zoom_out" aria-label="3D-Ansicht verkleinern" @click="zoom(1.25)" />
      <q-separator vertical />
      <q-btn flat no-caps label="Isometrie" aria-label="Isometrische Ansicht" @click="setView('iso')" />
      <q-btn flat no-caps label="Vorne" aria-label="Ansicht von vorne" @click="setView('front')" />
      <q-btn flat no-caps label="Rechts" aria-label="Ansicht von rechts" @click="setView('right')" />
      <q-btn flat no-caps label="Oben" aria-label="Ansicht von oben" @click="setView('top')" />
    </div>
  </section>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import AppStateMessage from 'src/shared/ui/AppStateMessage.vue'
import { apiErrorMessage } from 'src/utils/api-errors'
import { parseOffBuffer } from 'src/utils/off'
import { StlValidationError, validateStlBuffer, validateStlGeometry } from 'src/utils/stl'

defineOptions({ name: 'StlViewerPanel' })

const props = defineProps({
  src: { type: String, required: true },
  format: { type: String, default: 'stl', validator: (value) => ['off', 'stl'].includes(value) }
})

const $q = useQuasar()
const canvasHost = ref(null)
const loading = ref(true)
const errorMessage = ref('')
let abortController
let camera
let controls
let geometry
let material
let mesh
let renderer
let resizeObserver
let scene
let modelRadius = 1
let loadVersion = 0
let activeView = 'iso'

const errorMessages = {
  invalid: 'Die Modelldatei enthält keine gültige Geometrie.',
  tooLarge: 'Die Modelldatei ist für die 3D-Ansicht zu groß.',
  tooComplex: 'Das Modell enthält zu viele Dreiecke für eine sichere Darstellung.',
  unavailable: 'Die Modelldatei konnte nicht geladen werden.',
  webgl: 'Die 3D-Darstellung wird von diesem Browser oder Gerät nicht unterstützt.'
}

function dispose() {
  abortController?.abort()
  abortController = null
  resizeObserver?.disconnect()
  resizeObserver = null
  controls?.removeEventListener('change', renderScene)
  controls?.stopListenToKeyEvents()
  controls?.dispose()
  controls = null
  geometry?.dispose()
  geometry = null
  material?.dispose()
  material = null
  renderer?.dispose()
  renderer?.forceContextLoss()
  renderer?.domElement?.remove()
  renderer = null
  camera = null
  mesh = null
  scene = null
}

function renderScene() {
  if (renderer && scene && camera) renderer.render(scene, camera)
}

function resize() {
  if (!renderer || !camera || !canvasHost.value) return
  const width = Math.max(1, canvasHost.value.clientWidth)
  const height = Math.max(1, canvasHost.value.clientHeight)
  renderer.setSize(width, height, false)
  camera.aspect = width / height
  camera.updateProjectionMatrix()
  renderScene()
}

function setView(view) {
  if (!camera || !controls) return
  activeView = view
  const positions = {
    iso: [1, -1, 1],
    front: [0, -1, 0],
    right: [1, 0, 0],
    top: [0, -0.001, 1]
  }
  const [x, y, z] = positions[view] || positions.iso
  const verticalFov = camera.fov * Math.PI / 180
  const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * camera.aspect)
  const distance = Math.max(
    modelRadius * 1.15 / Math.sin(Math.min(verticalFov, horizontalFov) / 2),
    1
  )
  camera.position.set(x, y, z).normalize().multiplyScalar(distance)
  camera.near = Math.max(distance / 1000, 0.001)
  camera.far = Math.max(distance * 100, 1000)
  camera.updateProjectionMatrix()
  controls.target.set(0, 0, 0)
  controls.update()
}

function zoom(factor) {
  if (!camera || !controls) return
  const offset = camera.position.clone().sub(controls.target)
  const distance = Math.min(
    controls.maxDistance,
    Math.max(controls.minDistance, offset.length() * factor)
  )
  camera.position.copy(controls.target).add(offset.setLength(distance))
  controls.update()
  renderer?.domElement.focus()
}

async function load() {
  const version = ++loadVersion
  dispose()
  loading.value = true
  errorMessage.value = ''
  if (!props.src || !canvasHost.value) return
  const request = new AbortController()
  abortController = request
  try {
    const responsePromise = fetch(props.src, { signal: request.signal })
    const [THREE, { OrbitControls }, stlModule, response] = await Promise.all([
      import('three'),
      import('three/examples/jsm/controls/OrbitControls.js'),
      props.format === 'stl' ? import('three/examples/jsm/loaders/STLLoader.js') : null,
      responsePromise
    ])
    if (!response.ok) {
      let payload
      try {
        payload = await response.json()
      } catch {
        payload = { response: { status: response.status } }
      }
      const error = new StlValidationError('unavailable')
      error.displayMessage = apiErrorMessage(payload, errorMessages.unavailable)
      throw error
    }
    const buffer = await response.arrayBuffer()
    if (version !== loadVersion) return

    if (props.format === 'off') {
      const parsed = parseOffBuffer(buffer)
      geometry = new THREE.BufferGeometry()
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(parsed.positions, 3))
      if (parsed.colors) geometry.setAttribute('color', new THREE.Float32BufferAttribute(parsed.colors, 3))
      validateStlGeometry(geometry)
    } else {
      validateStlBuffer(buffer)
      geometry = new stlModule.STLLoader().parse(buffer)
      validateStlGeometry(geometry)
    }
    geometry.center()
    geometry.computeVertexNormals()
    modelRadius = geometry.boundingSphere.radius

    scene = new THREE.Scene()
    scene.background = new THREE.Color($q.dark.isActive ? 0x111827 : 0xf1f5f9)
    camera = new THREE.PerspectiveCamera(42, 1, 0.01, 1000)
    camera.up.set(0, 0, 1)
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true })
    } catch {
      throw new StlValidationError('webgl')
    }
    renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 2))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.domElement.tabIndex = 0
    renderer.domElement.setAttribute(
      'aria-label',
      '3D-Modell. Pfeiltasten verschieben die Ansicht, Umschalt und Pfeiltasten drehen sie.'
    )
    canvasHost.value.appendChild(renderer.domElement)

    material = new THREE.MeshStandardMaterial({
      color: geometry.getAttribute('color') ? 0xffffff : 0x42a5f5,
      vertexColors: Boolean(geometry.getAttribute('color')),
      metalness: 0.08,
      roughness: 0.72
    })
    mesh = new THREE.Mesh(geometry, material)
    scene.add(mesh)
    scene.add(new THREE.HemisphereLight(0xffffff, 0x334155, 2.2))
    const light = new THREE.DirectionalLight(0xffffff, 2.6)
    light.position.set(3, -4, 5)
    scene.add(light)

    controls = new OrbitControls(camera, renderer.domElement)
    controls.screenSpacePanning = true
    controls.minDistance = Math.max(modelRadius * 0.2, 0.01)
    controls.maxDistance = Math.max(modelRadius * 30, 10)
    controls.addEventListener('change', renderScene)
    controls.listenToKeyEvents(renderer.domElement)
    resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(canvasHost.value)
    resize()
    setView(activeView)
    loading.value = false
  } catch (error) {
    if (error?.name === 'AbortError' || version !== loadVersion) return
    dispose()
    errorMessage.value = error.displayMessage || errorMessages[error?.code] || errorMessages.unavailable
    loading.value = false
  }
}

watch(() => [props.src, props.format], load)
watch(() => $q.dark.isActive, (dark) => {
  if (scene) {
    scene.background.set(dark ? 0x111827 : 0xf1f5f9)
    renderScene()
  }
})
onMounted(load)
onBeforeUnmount(() => {
  loadVersion += 1
  dispose()
})
</script>

<style scoped>
.stl-viewer {
  min-height: 260px;
  overflow: hidden;
  border-radius: 8px;
  background: var(--app-surface-tonal);
}

.stl-viewer__canvas {
  touch-action: none;
}

.stl-viewer__canvas :deep(canvas) {
  display: block;
  width: 100%;
  height: 100%;
}

.stl-viewer__canvas :deep(canvas:focus-visible) {
  outline: 2px solid var(--q-primary);
  outline-offset: -2px;
}

.stl-viewer__state {
  position: absolute;
  inset: 0;
  background: var(--app-surface-tonal);
}

.stl-viewer__controls {
  position: absolute;
  right: 12px;
  bottom: 12px;
  left: 12px;
  overflow-x: auto;
  border: 1px solid var(--app-surface-border);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(15, 23, 42, 0.18);
}

@media (pointer: coarse) {
  .stl-viewer__controls :deep(.q-btn) {
    min-height: 44px;
  }
}
</style>
