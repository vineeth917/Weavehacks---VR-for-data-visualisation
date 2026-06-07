let recognition = null;
let micBtn = null;
let listening = false;
let onQueryCallback = null;

let micStateListener = null;

function setListening(active) {
  listening = active;
  micStateListener?.(active);
  if (micBtn) {
    micBtn.textContent = active ? 'Listening...' : 'Enable Mic';
    micBtn.style.background = active ? '#cc0000' : '#333';
  }
}

export function initVoice(onQueryCallbackFn) {
  onQueryCallback = onQueryCallbackFn;

  navigator.mediaDevices
    .getUserMedia({ audio: true })
    .then(() => {
      console.log('Mic permission granted');
    })
    .catch(() => {
      console.warn('Mic permission denied');
    });

  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!SpeechRecognition) {
    console.warn('SpeechRecognition not supported');
    return;
  }

  recognition = new SpeechRecognition();
  recognition.lang = 'en-US';
  recognition.continuous = false;
  recognition.interimResults = false;

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    onQueryCallback?.(transcript);
  };

  recognition.onerror = (event) => {
    console.error(event.error);
    setListening(false);
  };

  recognition.onend = () => {
    setListening(false);
  };

  micBtn = document.getElementById('mic-btn');
  micBtn?.addEventListener('click', () => startListening());
}

export function startListening() {
  if (!recognition || listening) return;
  try {
    recognition.start();
    setListening(true);
  } catch (err) {
    console.warn('Could not start recognition', err);
  }
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
