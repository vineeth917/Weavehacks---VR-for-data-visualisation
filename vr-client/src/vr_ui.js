import * as THREE from 'three';

let hudGroup = null;
let statusSprite = null;
let micButtonMesh = null;

function createLabelTexture(text, { width = 512, height = 128, fontSize = 28, bg = '#222222', color = '#ffffff' } = {}) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');

  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = color;
  ctx.font = `${fontSize}px sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, width / 2, height / 2);

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

function createButton(label, action, x, y, color = '#333333') {
  const texture = createLabelTexture(label, { bg: color, fontSize: 32, height: 96 });
  const material = new THREE.MeshBasicMaterial({ map: texture, transparent: true });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(0.28, 0.07), material);
  mesh.position.set(x, y, 0);
  mesh.userData.interactable = true;
  mesh.userData.kind = 'ui';
  mesh.userData.action = action;
  return mesh;
}

function updateStatus(text, color = '#ffffff') {
  if (!statusSprite) return;
  const texture = createLabelTexture(text, { fontSize: 22, height: 96, color });
  statusSprite.material.map?.dispose();
  statusSprite.material.map = texture;
  statusSprite.material.needsUpdate = true;
}

function setMicListening(listening) {
  if (!micButtonMesh) return;
  const texture = createLabelTexture(
    listening ? 'Listening…' : 'Mic',
    { bg: listening ? '#cc0000' : '#2a5a2a', fontSize: 32, height: 96 }
  );
  micButtonMesh.material.map?.dispose();
  micButtonMesh.material.map = texture;
  micButtonMesh.material.needsUpdate = true;
}

export function initVRUI(camera, renderer, { onMic, onExit, onLoadCharts, onStatusChange }) {
  hudGroup = new THREE.Group();
  hudGroup.name = 'vrHud';
  camera.add(hudGroup);
  hudGroup.position.set(0, -0.25, -0.65);

  statusSprite = new THREE.Mesh(
    new THREE.PlaneGeometry(0.55, 0.07),
    new THREE.MeshBasicMaterial({
      map: createLabelTexture('Loading…', { fontSize: 22, height: 96 }),
      transparent: true,
    })
  );
  statusSprite.position.set(0, 0.12, 0);
  hudGroup.add(statusSprite);

  micButtonMesh = createButton('Mic', 'mic', -0.22, -0.05, '#2a5a2a');
  hudGroup.add(micButtonMesh);

  const loadButton = createButton('Charts', 'load_eda', 0, -0.05, '#2a3a5a');
  hudGroup.add(loadButton);

  const exitButton = createButton('Exit VR', 'exit', 0.22, -0.05, '#5a2a2a');
  hudGroup.add(exitButton);

  const hint = new THREE.Mesh(
    new THREE.PlaneGeometry(0.55, 0.05),
    new THREE.MeshBasicMaterial({
      map: createLabelTexture('Trigger = select · Grip = grab region', {
        fontSize: 18,
        height: 64,
        color: '#aaaaaa',
        bg: '#111111',
      }),
      transparent: true,
    })
  );
  hint.position.set(0, -0.12, 0);
  hudGroup.add(hint);

  onStatusChange?.((connected) => {
    updateStatus(connected ? 'Backend connected' : 'Backend disconnected', connected ? '#88ff88' : '#ff8888');
  });

  return {
    setMicListening,
    updateStatus,
    handleUIAction(action) {
      if (action === 'mic') onMic?.();
      if (action === 'load_eda') onLoadCharts?.();
      if (action === 'exit') {
        const session = renderer.xr.getSession();
        if (session) session.end();
      }
    },
  };
}

export function getHudGroup() {
  return hudGroup;
}
