import * as THREE from 'three';
import { scene } from './scene.js';

const panelMeshes = {};

function toDataUri(b64) {
  if (!b64 || typeof b64 !== 'string') return null;
  const trimmed = b64.trim();
  if (!trimmed) return null;
  if (trimmed.startsWith('data:')) return trimmed;
  return `data:image/png;base64,${trimmed}`;
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Failed to load image: ${src.slice(0, 48)}...`));
    img.src = src;
  });
}

function drawPanelCanvas(ctx, panel, chartImage) {
  ctx.fillStyle = '#1a1a1a';
  ctx.fillRect(0, 0, 512, 512);

  const titleHeight = 100;
  const padding = 12;

  if (chartImage) {
    const chartTop = titleHeight;
    const chartHeight = 512 - titleHeight - padding;
    const chartWidth = 512 - padding * 2;

    ctx.fillStyle = '#0d0d0d';
    ctx.fillRect(padding, chartTop, chartWidth, chartHeight);

    const scale = Math.min(chartWidth / chartImage.width, chartHeight / chartImage.height);
    const drawW = chartImage.width * scale;
    const drawH = chartImage.height * scale;
    const drawX = padding + (chartWidth - drawW) / 2;
    const drawY = chartTop + (chartHeight - drawH) / 2;

    ctx.drawImage(chartImage, drawX, drawY, drawW, drawH);
  } else {
    ctx.fillStyle = '#ffffff';
    ctx.font = '28px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText(panel.title || '', 256, 40);

    ctx.fillStyle = '#aaaaaa';
    ctx.font = '20px sans-serif';
    ctx.fillText(panel.column || '', 256, 85);

    ctx.fillStyle = '#555555';
    ctx.font = '18px sans-serif';
    ctx.fillText('No chart image', 256, 260);
  }

  if (chartImage) {
    ctx.fillStyle = 'rgba(0, 0, 0, 0.65)';
    ctx.fillRect(0, 0, 512, titleHeight);

    ctx.fillStyle = '#ffffff';
    ctx.font = '24px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(panel.title || '', 256, 38);

    ctx.fillStyle = '#aaaaaa';
    ctx.font = '16px sans-serif';
    ctx.fillText(panel.column || '', 256, 68);
  }

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
}

function createPanelTexture(panel) {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 512;
  const ctx = canvas.getContext('2d');

  drawPanelCanvas(ctx, panel, null);

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;

  const dataUri = toDataUri(panel.image_b64);
  if (dataUri) {
    loadImage(dataUri)
      .then((img) => {
        drawPanelCanvas(ctx, panel, img);
        texture.needsUpdate = true;
      })
      .catch((err) => {
        console.warn(`Panel ${panel.id}: chart image failed to load`, err);
      });
  }

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
