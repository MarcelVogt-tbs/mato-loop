export function createVoiceClient({ negotiate, onPhase, onControl, onError, onClose, onWakeLock = () => {} }) {
  let peer = null, stream = null, channel = null, control = null, timer = null, startup = null
  let wakeLock = null
  let closed = false, ready = false, muted = false, connected = false
  const abort = new AbortController()
  const audio = new Audio()
  audio.autoplay = true
  audio.setAttribute('playsinline', '')

  function updateMicrophone() {
    for (const track of stream?.getAudioTracks() || []) track.enabled = !closed && ready && connected && !muted
  }
  function fail(code) {
    if (closed) return
    close()
    onError(code)
  }
  function hidden() {
    if (document.hidden) { close(); onClose() }
  }
  function pagehide() { close(); onClose() }
  function releaseWakeLock(lock) {
    void lock.release().catch(() => console.warn('Screen wake lock could not be released.'))
  }
  async function keepScreenAwake() {
    if (!navigator.wakeLock?.request) { onWakeLock(false); return }
    try {
      const lock = await navigator.wakeLock.request('screen')
      if (closed) { releaseWakeLock(lock); return }
      wakeLock = lock
      lock.addEventListener('release', () => {
        wakeLock = null
        if (!closed) onWakeLock(false)
      })
      onWakeLock(!lock.released)
    } catch {
      if (!closed) onWakeLock(false)
    }
  }
  function close() {
    if (closed) return
    closed = true
    abort.abort()
    clearInterval(timer)
    clearTimeout(startup)
    if (wakeLock) { releaseWakeLock(wakeLock); wakeLock = null }
    for (const track of stream?.getTracks() || []) track.stop()
    audio.pause()
    audio.srcObject = null
    channel?.close()
    peer?.close()
    control?.close()
    document.removeEventListener('visibilitychange', hidden)
    window.removeEventListener('pagehide', pagehide)
  }
  return {
    close,
    mute(value) { muted = value; updateMicrophone() },
    interrupt() {
      if (control?.readyState === WebSocket.OPEN) control.send('interrupt')
    },
    async start(sessionId) {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) {
        fail('voice.browserUnsupported')
        return
      }
      document.addEventListener('visibilitychange', hidden)
      window.addEventListener('pagehide', pagehide)
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false })
        if (closed) { for (const track of stream.getTracks()) track.stop(); return }
        for (const track of stream.getAudioTracks()) track.enabled = false
        void keepScreenAwake()
        peer = new RTCPeerConnection()
        peer.ontrack = (event) => {
          if (closed) return
          audio.srcObject = event.streams[0] || new MediaStream([event.track])
          void audio.play().catch(() => fail('voice.playbackFailed'))
        }
        peer.onconnectionstatechange = () => {
          if (closed) return
          connected = peer.connectionState === 'connected'
          updateMicrophone()
          if (connected && ready) clearTimeout(startup)
          if (['failed', 'disconnected', 'closed'].includes(peer.connectionState)) fail('voice.connectionLost')
        }
        for (const track of stream.getAudioTracks()) {
          peer.addTrack(track, stream)
          track.onended = () => fail('voice.microphoneFailed')
        }
        channel = peer.createDataChannel('oai-events')
        channel.onclose = () => { if (!closed) fail('voice.connectionLost') }
        // The backend owns transcripts, instructions and tools; never dispatch browser tool events.
        const offer = await peer.createOffer()
        await peer.setLocalDescription(offer)
        const result = await negotiate(sessionId, { sdp_offer: offer.sdp }, abort.signal)
        if (closed) return
        await peer.setRemoteDescription({ type: 'answer', sdp: result.sdpAnswer })
        const url = new URL(`sessions/${sessionId}/voice/${result.connectionId}/control`, new URL('/api/', window.location.href))
        url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
        control = new WebSocket(url)
        startup = setTimeout(() => fail('voice.startFailed'), 45_000)
        control.onopen = () => {
          if (closed) return
          control.send('heartbeat')
          timer = setInterval(() => { if (control.readyState === WebSocket.OPEN) control.send('heartbeat') }, 5000)
        }
        control.onmessage = ({ data }) => {
          if (closed) return
          let state
          try { state = JSON.parse(data) } catch { fail('voice.protocolError'); return }
          onControl(state)
          if (state.errorCode) { fail(state.errorCode); return }
          if (state.phase === 'ended' || state.phase === 'closing') { close(); onClose(); return }
          ready = state.phase !== 'starting'
          updateMicrophone()
          if (ready && connected) clearTimeout(startup)
          onPhase(ready && connected ? state.phase : 'starting')
        }
        control.onerror = () => fail('voice.connectionLost')
        control.onclose = () => { if (!closed) fail('voice.connectionLost') }
      } catch (error) {
        if (closed) return
        if (error?.isAxiosError) { close(); throw error }
        fail(error?.name === 'NotAllowedError' ? 'voice.microphoneDenied' : 'voice.microphoneFailed')
      }
    }
  }
}
