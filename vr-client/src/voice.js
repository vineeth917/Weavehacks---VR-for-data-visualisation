export function initVoice(onQueryCallback) {
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
  const recognition = new SpeechRecognition();
  recognition.lang = 'en-US';
  recognition.continuous = false;
  recognition.interimResults = false;

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    onQueryCallback(transcript);
  };

  recognition.onerror = (event) => {
    console.error(event.error);
  };

  const micBtn = document.getElementById('mic-btn');
  micBtn.addEventListener('click', () => {
    recognition.start();
    micBtn.textContent = 'Listening...';
    micBtn.style.background = '#cc0000';
  });

  recognition.onend = () => {
    micBtn.textContent = 'Enable Mic';
    micBtn.style.background = '#333';
  };
}

export function speak(text) {
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 0.95;
  utterance.pitch = 1.0;
  window.speechSynthesis.speak(utterance);
}

export function stopSpeaking() {
  window.speechSynthesis.cancel();
}
