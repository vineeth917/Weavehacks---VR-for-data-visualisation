import * as THREE from 'three';
import { scene } from './scene.js';

const panelMeshes = {};

function createPanelTexture(panel) {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 512;
  const ctx = canvas.getContext('2d');

  ctx.fillStyle = '#1a1a1a';
  ctx.fillRect(0, 0, 512, 512);

  const hasAlertFlags =
    panel.flags?.includes('right_skewed') || panel.flags?.includes('outliers');

  if (hasAlertFlags) {
    ctx.strokeStyle = '#E24B4A';
    ctx.lineWidth = 8;
    ctx.strokeRect(4, 4, 504, 504);
  } else {
    ctx.strokeStyle = '#444444';
    ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, 510, 510);
  }

  ctx.fillStyle = '#ffffff';
  ctx.font = '28px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.fillText(panel.title || '', 256, 40);

  ctx.fillStyle = '#aaaaaa';
  ctx.font = '20px sans-serif';
  ctx.fillText(panel.column || '', 256, 85);

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

function disposePanelMesh(id) {
  const mesh = panelMeshes[id];
  if (!mesh) return;

  scene.remove(mesh);
  mesh.geometry.dispose();
  mesh.material.map?.dispose();
  mesh.material.dispose();
  delete panelMeshes[id];
}

export function getPanelMesh(id) {
  return panelMeshes[id] ?? null;
}

export function renderPanels(msg) {
  clearPanels();

  const panels = msg.panels || [];
  const total = panels.length;

  panels.forEach((panel, i) => {
    const angle = (i - (total - 1) / 2) * 0.4;
    const x = Math.sin(angle) * 3;
    const y = 1.6;
    const z = -Math.cos(angle) * 3;

    const texture = createPanelTexture(panel);
    const geometry = new THREE.PlaneGeometry(1.2, 1.2);
    const material = new THREE.MeshBasicMaterial({
      map: texture,
      side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(geometry, material);

    mesh.position.set(x, y, z);
    mesh.lookAt(0, 1.6, 0);

    mesh.userData.interactable = true;
    mesh.userData.kind = 'panel';
    mesh.userData.id = panel.id;

    scene.add(mesh);
    panelMeshes[panel.id] = mesh;
  });
}

export function highlightPanel(targetIds) {
  const ids = Array.isArray(targetIds) ? targetIds : [targetIds];

  ids.forEach((id) => {
    const mesh = panelMeshes[id];
    if (!mesh) return;

    mesh.scale.set(1.15, 1.15, 1.15);

    setTimeout(() => {
      mesh.scale.set(1.0, 1.0, 1.0);
    }, 800);
  });
}

export function clearPanels() {
  Object.keys(panelMeshes).forEach(disposePanelMesh);
}
