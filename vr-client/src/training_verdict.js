import * as THREE from 'three';
import { scene } from './scene.js';

const VERDICTS = {
  0: { label: 'Healthy', color: 0x2ecc71 },
  1: { label: 'Overfitting', color: 0xe74c3c },
  2: { label: 'Underfitting', color: 0x3498db },
  3: { label: 'Leakage', color: 0xf39c12 },
  [-1]: { label: 'Unknown', color: 0x888888 },
};

const STEP_SPACING = 0.012;
const historyCache = {};

let displayGroup = null;

function isFiniteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function disposeObject(obj) {
  if (obj.geometry) obj.geometry.dispose();
  if (obj.material) {
    const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
    materials.forEach((m) => {
      m.map?.dispose();
      m.dispose();
    });
  }
}

function createTextSprite(text, { fontSize = 28, color = '#ffffff', width = 512, height = 128 } = {}) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');

  ctx.font = `${fontSize}px sans-serif`;
  ctx.fillStyle = color;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, width / 2, height / 2);

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(1.2, 0.3, 1);
  return sprite;
}

function createVerdictPanel(verdict, step, runId) {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 256;
  const ctx = canvas.getContext('2d');

  ctx.fillStyle = '#111111';
  ctx.fillRect(0, 0, 512, 256);

  const hex = `#${verdict.color.toString(16).padStart(6, '0')}`;
  ctx.strokeStyle = hex;
  ctx.lineWidth = 10;
  ctx.strokeRect(5, 5, 502, 246);

  ctx.fillStyle = hex;
  ctx.font = 'bold 36px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(verdict.label, 256, 90);

  ctx.fillStyle = '#cccccc';
  ctx.font = '22px sans-serif';
  if (runId) ctx.fillText(runId, 256, 140);
  if (isFiniteNumber(step)) ctx.fillText(`step ${step}`, 256, 175);

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  const material = new THREE.MeshBasicMaterial({ map: texture, side: THREE.DoubleSide });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(0.9, 0.45), material);
  mesh.position.set(0, 1.6, 0);
  return mesh;
}

function readHistoryPoint(point) {
  const step = point.step ?? point.epoch;
  const train = point.train_loss ?? point.trainLoss ?? point.train;
  const val = point.val_loss ?? point.valLoss ?? point.val;
  if (!isFiniteNumber(step) || !isFiniteNumber(train) || !isFiniteNumber(val)) return null;
  return { step, train, val };
}

function normalizeHistoryEntry(entry) {
  const raw = Array.isArray(entry) ? entry : entry?.history ?? entry?.steps ?? entry?.updates;
  if (!Array.isArray(raw)) return [];

  return raw.map(readHistoryPoint).filter(Boolean);
}

async function loadReplayHistory(runId) {
  if (historyCache[runId]) return historyCache[runId];

  const urls = ['/data/replay_run_history.json', '/mocks/replay_run_history.json'];
  for (const url of urls) {
    try {
      const resp = await fetch(url);
      if (!resp.ok) continue;
      const data = await resp.json();
      const entry = data[runId];
      if (!entry) continue;
      const history = normalizeHistoryEntry(entry);
      if (history.length >= 2) {
        historyCache[runId] = history;
        return history;
      }
    } catch {
      // try next url
    }
  }
  return null;
}

function drawStaticCurve(history) {
  const trainPoints = [];
  const valPoints = [];

  history.forEach(({ step, train, val }) => {
    const z = -step * STEP_SPACING;
    trainPoints.push(new THREE.Vector3(0, train * 2, z));
    valPoints.push(new THREE.Vector3(0.15, val * 2, z));
  });

  if (trainPoints.length >= 2) {
    const geometry = new THREE.BufferGeometry().setFromPoints(trainPoints);
    geometry.computeBoundingSphere();
    const line = new THREE.Line(
      geometry,
      new THREE.LineBasicMaterial({ color: 0x185fa5 })
    );
    displayGroup.add(line);
  }

  if (valPoints.length >= 2) {
    const geometry = new THREE.BufferGeometry().setFromPoints(valPoints);
    geometry.computeBoundingSphere();
    const line = new THREE.Line(
      geometry,
      new THREE.LineBasicMaterial({ color: 0xe24b4a })
    );
    displayGroup.add(line);
  }

  const trainLabel = createTextSprite('train', { fontSize: 24, color: '#185fa5' });
  trainLabel.position.set(-0.15, 0.3, 0);
  displayGroup.add(trainLabel);

  const valLabel = createTextSprite('val', { fontSize: 24, color: '#e24b4a' });
  valLabel.position.set(0.35, 0.3, 0);
  displayGroup.add(valLabel);
}

function readVerdictCode(msg) {
  const code = msg?.metrics?.verdict_code;
  return isFiniteNumber(code) ? code : -1;
}

export function initTrainingDisplay() {
  clearTrainingDisplay();
  displayGroup = new THREE.Group();
  displayGroup.position.set(-2, 1.2, -1);
  scene.add(displayGroup);
}

export async function showTrainingVerdict(msg) {
  if (!displayGroup) initTrainingDisplay();
  else {
    clearTrainingDisplay();
    initTrainingDisplay();
  }

  const code = readVerdictCode(msg);
  const verdict = VERDICTS[code] ?? VERDICTS[-1];

  const panel = createVerdictPanel(verdict, msg.step, msg.run_id);
  displayGroup.add(panel);

  if (msg.run_id) {
    const history = await loadReplayHistory(msg.run_id);
    if (history) {
      drawStaticCurve(history);
    }
  }
}

export function clearTrainingDisplay() {
  if (displayGroup) {
    scene.remove(displayGroup);
    displayGroup.traverse(disposeObject);
  }
  displayGroup = null;
}
