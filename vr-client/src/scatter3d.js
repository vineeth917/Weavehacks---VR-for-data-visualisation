import * as THREE from 'three';
import { scene } from './scene.js';

let scatterGroup = null;
const pointMeshes = {};

function createAxisLine(from, to, color) {
  const geometry = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(...from),
    new THREE.Vector3(...to),
  ]);
  const material = new THREE.LineBasicMaterial({ color });
  return new THREE.Line(geometry, material);
}

function createAxisLabel(text) {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');

  ctx.font = '24px sans-serif';
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
  sprite.scale.set(0.4, 0.1, 1);
  return sprite;
}

function disposeScatterGroup(group) {
  if (!group) return;

  scene.remove(group);
  group.traverse((obj) => {
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
  });
}

export function getPointMesh(id) {
  return pointMeshes[id] ?? null;
}

export function getPointMeshes() {
  return Object.values(pointMeshes);
}

export function getNearbyPointIds(worldPos, radius) {
  const ids = [];

  for (const [id, mesh] of Object.entries(pointMeshes)) {
    const pos = new THREE.Vector3();
    mesh.getWorldPosition(pos);
    if (pos.distanceTo(worldPos) <= radius) {
      ids.push(id);
    }
  }

  return ids;
}

export function clearScatter() {
  if (scatterGroup) {
    disposeScatterGroup(scatterGroup);
    scatterGroup = null;
  }
  Object.keys(pointMeshes).forEach((key) => delete pointMeshes[key]);
}

export function renderScatter(msg) {
  clearScatter();

  scatterGroup = new THREE.Group();
  scatterGroup.position.set(0, 1.0, -2);

  const points = msg.points || [];
  points.forEach((point) => {
    const geometry = new THREE.SphereGeometry(point.size || 0.04, 16, 16);
    const material = new THREE.MeshStandardMaterial({ color: point.color });
    const mesh = new THREE.Mesh(geometry, material);

    mesh.position.set(
      point.x * 1.5,
      point.y * 1.5,
      point.z * 1.5
    );

    const id = point.id || point.label;
    mesh.userData.interactable = true;
    mesh.userData.kind = 'point';
    mesh.userData.id = id;
    mesh.userData.label = point.label;

    scatterGroup.add(mesh);
    pointMeshes[id] = mesh;
  });

  const axes = [
    { from: [0, 0, 0], to: [1.6, 0, 0], color: 0xff4444, label: msg.axes?.x, pos: [1.75, 0, 0] },
    { from: [0, 0, 0], to: [0, 1.6, 0], color: 0x44ff44, label: msg.axes?.y, pos: [0, 1.75, 0] },
    { from: [0, 0, 0], to: [0, 0, 1.6], color: 0x4444ff, label: msg.axes?.z, pos: [0, 0, 1.75] },
  ];

  axes.forEach(({ from, to, color, label, pos }) => {
    scatterGroup.add(createAxisLine(from, to, color));

    if (label) {
      const sprite = createAxisLabel(label);
      sprite.position.set(...pos);
      scatterGroup.add(sprite);
    }
  });

  scene.add(scatterGroup);
}
