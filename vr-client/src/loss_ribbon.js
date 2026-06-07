import * as THREE from 'three';
import { scene } from './scene.js';

let trainPoints = [];
let valPoints = [];
let ribbonGroup = null;
let trainLine = null;
let valLine = null;
let currentRunId = null;
const STEP_SPACING = 0.05;

function isFiniteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

export function readLosses(msg) {
  const metrics = msg?.metrics;
  if (!metrics || typeof metrics !== 'object') return null;

  const train = metrics.train_loss ?? metrics.trainLoss ?? metrics.train;
  const val = metrics.val_loss ?? metrics.valLoss ?? metrics.val;
  const step = msg?.step;

  if (!isFiniteNumber(train) || !isFiniteNumber(val) || !isFiniteNumber(step)) {
    return null;
  }

  return { step, train, val };
}

function finitePoints(points) {
  return points.filter(
    (p) => isFiniteNumber(p.x) && isFiniteNumber(p.y) && isFiniteNumber(p.z)
  );
}

function createLabel(text) {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 128;
  const ctx = canvas.getContext('2d');

  ctx.font = '32px sans-serif';
  ctx.fillStyle = '#ffffff';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, canvas.width / 2, canvas.height / 2);

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(0.8, 0.2, 1);
  return sprite;
}

function disposeObject(obj) {
  if (obj.geometry) obj.geometry.dispose();
  if (obj.material) {
    const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
    materials.forEach((material) => {
      material.map?.dispose();
      material.dispose();
    });
  }
}

function rebuildLines() {
  if (!ribbonGroup) return;

  if (trainLine) {
    ribbonGroup.remove(trainLine);
    disposeObject(trainLine);
    trainLine = null;
  }

  if (valLine) {
    ribbonGroup.remove(valLine);
    disposeObject(valLine);
    valLine = null;
  }

  const trainFinite = finitePoints(trainPoints);
  const valFinite = finitePoints(valPoints);

  if (trainFinite.length >= 2) {
    const geometry = new THREE.BufferGeometry().setFromPoints(trainFinite);
    geometry.computeBoundingSphere();
    trainLine = new THREE.Line(
      geometry,
      new THREE.LineBasicMaterial({ color: 0x185fa5 })
    );
    ribbonGroup.add(trainLine);
  }

  if (valFinite.length >= 2) {
    const geometry = new THREE.BufferGeometry().setFromPoints(valFinite);
    geometry.computeBoundingSphere();
    valLine = new THREE.Line(
      geometry,
      new THREE.LineBasicMaterial({ color: 0xe24b4a })
    );
    ribbonGroup.add(valLine);
  }
}

export function initRibbon() {
  clearRibbon();

  ribbonGroup = new THREE.Group();
  ribbonGroup.position.set(-2, 1.2, -1);
  scene.add(ribbonGroup);

  const trainLabel = createLabel('train');
  trainLabel.position.set(-0.15, 0.35, 0.2);
  ribbonGroup.add(trainLabel);

  const valLabel = createLabel('val');
  valLabel.position.set(0.35, 0.35, 0.2);
  ribbonGroup.add(valLabel);

  trainPoints = [];
  valPoints = [];
  currentRunId = null;
}

export function resetRibbonRun(runId) {
  if (currentRunId === runId) return;
  currentRunId = runId ?? null;
  trainPoints = [];
  valPoints = [];
  rebuildLines();
}

export function addTrainingStep(msg) {
  const losses = readLosses(msg);
  if (!losses) {
    console.warn('training_update skipped: missing finite train_loss/val_loss/step', msg);
    return;
  }

  if (!ribbonGroup) initRibbon();

  if (msg.run_id && msg.run_id !== currentRunId) {
    resetRibbonRun(msg.run_id);
  }

  const z = -losses.step * STEP_SPACING;
  trainPoints.push(new THREE.Vector3(0, losses.train * 2, z));
  valPoints.push(new THREE.Vector3(0.15, losses.val * 2, z));

  rebuildLines();

  if (msg.status === 'done' || msg.status === 'stopped') {
    const markerGeometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-0.05, 0, z),
      new THREE.Vector3(0.25, losses.val * 2 + 0.15, z),
    ]);
    const markerLine = new THREE.Line(
      markerGeometry,
      new THREE.LineBasicMaterial({ color: 0xffffff })
    );
    ribbonGroup.add(markerLine);

    const label = createLabel(msg.status === 'stopped' ? 'Stopped' : 'Done');
    label.position.set(0.1, losses.val * 2 + 0.35, z);
    ribbonGroup.add(label);
  }
}

export function clearRibbon() {
  if (ribbonGroup) {
    scene.remove(ribbonGroup);
    ribbonGroup.traverse(disposeObject);
  }

  ribbonGroup = null;
  trainLine = null;
  valLine = null;
  trainPoints = [];
  valPoints = [];
  currentRunId = null;
}
