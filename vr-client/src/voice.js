import { TRANSCRIBE_URL } from './ws.js';

let recognition = null;
let micBtn = null;
let listening = false;
let onQueryCallback = null;
let micStateListener = null;
let mediaRecorder = null;
let recordTimeout = null;

const RECORD_MS = 4500;

function setListening(active) {
  listening = active;
  micStateListener?.(active);
  if (micBtn) {
    micBtn.textContent = active ? 'Listening...' : 'Enable Mic';
    micBtn.style.background = active ? '#cc0000' : '#333';
  }
}

function hasBrowserSTT() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

function audioFilename(mimeType) {
  if (mimeType?.includes('mp4')) return 'audio.mp4';
  if (mimeType?.includes('ogg')) return 'audio.ogg';
  return 'audio.webm';
}

async function transcribeBlob(blob, mimeType) {
  const filename = audioFilename(mimeType);
  const form = new FormData();
  form.append('file', blob, filename);
  form.append('audio', blob, filename);

  const resp = await fetch(TRANSCRIBE_URL, {
    method: 'POST',
    headers: { 'ngrok-skip-browser-warning': 'true' },
    body: form,
  });

  if (!resp.ok) {
    throw new Error(`Transcribe failed: ${resp.status}`);
  }

  const data = await resp.json();
  return data.text ?? data.transcript ?? data.result ?? '';
}

function pickRecorderMimeType() {
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) ?? '';
}

async function startBackendListening() {
  setListening(true);
  console.log('Recording for backend STT…');

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  audioChunks = [];
  const mimeType = pickRecorderMimeType();
  mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);

  mediaRecorder.ondataavailable = (event) => {
    if (event.data.size > 0) audioChunks.push(event.data);
  };

  mediaRecorder.onstop = async () => {
    stream.getTracks().forEach((track) => track.stop());
    clearTimeout(recordTimeout);
    recordTimeout = null;

    try {
      const mimeType = mediaRecorder.mimeType || 'audio/webm';
      const blob = new Blob(audioChunks, { type: mimeType });
      const text = (await transcribeBlob(blob, mimeType)).trim();
      console.log('Transcribed:', text);
      if (text) onQueryCallback?.(text);
    } catch (err) {
      console.error('Backend STT error', err);
    } finally {
      setListening(false);
      mediaRecorder = null;
    }
  };

  mediaRecorder.start();
  recordTimeout = setTimeout(() => {
    if (mediaRecorder?.state === 'recording') {
      mediaRecorder.stop();
    }
  }, RECORD_MS);
}

let audioChunks = [];

export function initVoice(onQueryCallbackFn) {
  onQueryCallback = onQueryCallbackFn;

  navigator.mediaDevices
    .getUserMedia({ audio: true })
    .then(() => console.log('Mic permission granted'))
    .catch(() => console.warn('Mic permission denied'));

  if (hasBrowserSTT()) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.lang = 'en-US';
    recognition.continuous = false;
    recognition.interimResults = false;

    recognition.onresult = (event) => {
      const transcript = event.results[0][0].transcript;
      onQueryCallback?.(transcript);
    };

    recognition.onerror = (event) => {
      console.error('Browser STT error', event.error);
      setListening(false);
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        startBackendListening().catch(console.error);
      }
    };

    recognition.onend = () => setListening(false);
  } else {
    console.log('Browser STT unavailable — using backend /transcribe');
  }

  micBtn = document.getElementById('mic-btn');
  micBtn?.addEventListener('click', () => startListening());
}

export function startListening() {
  if (listening) return;

  if (hasBrowserSTT() && recognition) {
    try {
      recognition.start();
      setListening(true);
      return;
    } catch (err) {
      console.warn('Browser STT failed, falling back to backend', err);
    }
  }

  startBackendListening().catch((err) => {
    console.error('Could not start recording', err);
    setListening(false);
  });
}

export function isListening() {
  return listening;
}

export function onMicStateChange(fn) {
  micStateListener = fn;
}

export function speak(text) {
  if (!text) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 0.95;
  utterance.pitch = 1.0;
  window.speechSynthesis.speak(utterance);
}

export function stopSpeaking() {
  window.speechSynthesis.cancel();
}
