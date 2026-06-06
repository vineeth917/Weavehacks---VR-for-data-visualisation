import * as THREE from 'three';
import { scene } from './scene.js';

let trainPoints = [];
let valPoints = [];
let ribbonGroup = null;
let trainLine = null;
let valLine = null;
const STEP_SPACING = 0.05;

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
  const material = new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
  });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(0.8, 0.2, 1);
  return sprite;
}

function disposeObject(obj) {
  if (obj.geometry) {
    obj.geometry.dispose();
  }
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

  if (trainPoints.length > 0) {
    const trainGeometry = new THREE.BufferGeometry().setFromPoints(trainPoints);
    const trainMaterial = new THREE.LineBasicMaterial({ color: 0x185FA5, linewidth: 2 });
    trainLine = new THREE.Line(trainGeometry, trainMaterial);
    ribbonGroup.add(trainLine);
  }

  if (valPoints.length > 0) {
    const valGeometry = new THREE.BufferGeometry().setFromPoints(valPoints);
    const valMaterial = new THREE.LineBasicMaterial({ color: 0xE24B4A, linewidth: 2 });
    valLine = new THREE.Line(valGeometry, valMaterial);
    ribbonGroup.add(valLine);
  }
}

export function initRibbon() {
  clearRibbon();

  ribbonGroup = new THREE.Group();
  ribbonGroup.position.set(-2, 1.2, -1);
  scene.add(ribbonGroup);

  trainPoints = [];
  valPoints = [];
}

export function addTrainingStep(msg) {
  if (!ribbonGroup) return;

  const z = -msg.step * STEP_SPACING;

  trainPoints.push(new THREE.Vector3(0, msg.metrics.train_loss * 2, z));
  valPoints.push(new THREE.Vector3(0.15, msg.metrics.val_loss * 2, z));

  rebuildLines();

  if (msg.status === 'done') {
    const markerGeometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0, z),
      new THREE.Vector3(0, 2, z),
    ]);
    const markerMaterial = new THREE.LineBasicMaterial({ color: 0xffffff });
    const markerLine = new THREE.Line(markerGeometry, markerMaterial);
    ribbonGroup.add(markerLine);

    const label = createLabel('Training complete');
    label.position.set(0, 2.2, z);
    ribbonGroup.add(label);
  }
}

export function clearRibbon() {
  if (ribbonGroup) {
    scene.remove(ribbonGroup);
    ribbonGroup.traverse((obj) => {
      disposeObject(obj);
    });
  }

  ribbonGroup = null;
  trainLine = null;
  valLine = null;
  trainPoints = [];
  valPoints = [];
}
