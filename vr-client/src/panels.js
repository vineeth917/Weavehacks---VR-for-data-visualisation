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

function chartArea() {
  return { top: 100, left: 12, width: 488, height: 400 };
}

function drawAxes(ctx, area) {
  ctx.strokeStyle = '#666666';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(area.left + 40, area.top + 20);
  ctx.lineTo(area.left + 40, area.top + area.height - 30);
  ctx.lineTo(area.left + area.width - 20, area.top + area.height - 30);
  ctx.stroke();
}

function drawMockChart(ctx, panel) {
  const area = chartArea();
  ctx.fillStyle = '#0d0d0d';
  ctx.fillRect(area.left, area.top, area.width, area.height);
  drawAxes(ctx, area);

  const kind = panel.kind || 'histogram';
  const plotLeft = area.left + 50;
  const plotBottom = area.top + area.height - 40;
  const plotWidth = area.width - 80;
  const plotHeight = area.height - 70;

  if (kind === 'histogram') {
    const bars = [0.15, 0.25, 0.4, 0.7, 0.95, 0.6, 0.35, 0.2, 0.12, 0.08];
    const barW = plotWidth / bars.length - 4;
    ctx.fillStyle = '#4a9eff';
    bars.forEach((h, i) => {
      const barH = h * plotHeight;
      ctx.fillRect(plotLeft + i * (barW + 4), plotBottom - barH, barW, barH);
    });
  } else if (kind === 'box') {
    const boxes = [
      { x: 0.15, low: 0.2, q1: 0.45, med: 0.55, q3: 0.7, high: 0.85 },
      { x: 0.45, low: 0.15, q1: 0.35, med: 0.5, q3: 0.65, high: 0.9 },
      { x: 0.75, low: 0.25, q1: 0.5, med: 0.6, q3: 0.75, high: 0.95 },
    ];
    ctx.strokeStyle = '#4a9eff';
    ctx.fillStyle = '#4a9eff';
    ctx.lineWidth = 2;
    boxes.forEach(({ x, low, q1, med, q3, high }) => {
      const cx = plotLeft + x * plotWidth;
      const y = (v) => plotBottom - v * plotHeight;
      ctx.beginPath();
      ctx.moveTo(cx, y(low));
      ctx.lineTo(cx, y(high));
      ctx.stroke();
      ctx.strokeRect(cx - 24, y(q3), 48, y(q1) - y(q3));
      ctx.beginPath();
      ctx.moveTo(cx - 24, y(med));
      ctx.lineTo(cx + 24, y(med));
      ctx.stroke();
    });
  } else if (kind === 'kde') {
    ctx.beginPath();
    ctx.moveTo(plotLeft, plotBottom);
    for (let i = 0; i <= 40; i++) {
      const t = i / 40;
      const x = plotLeft + t * plotWidth;
      const y = plotBottom - Math.exp(-((t - 0.55) ** 2) / 0.04) * plotHeight * 0.9;
      ctx.lineTo(x, y);
    }
    ctx.lineTo(plotLeft + plotWidth, plotBottom);
    ctx.closePath();
    ctx.fillStyle = 'rgba(74, 158, 255, 0.35)';
    ctx.fill();
    ctx.strokeStyle = '#4a9eff';
    ctx.lineWidth = 3;
    ctx.stroke();
  } else if (kind === 'missing') {
    const cols = 6;
    const rows = 5;
    const cellW = plotWidth / cols;
    const cellH = plotHeight / rows;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const missing = (r * cols + c) % 5 === 0 ? 0.85 : (r + c) % 7 === 0 ? 0.45 : 0.05;
        const g = Math.round(255 * (1 - missing));
        ctx.fillStyle = `rgb(${g}, ${g}, ${Math.round(g * 0.9)})`;
        ctx.fillRect(plotLeft + c * cellW, area.top + 30 + r * cellH, cellW - 2, cellH - 2);
      }
    }
  } else {
    ctx.fillStyle = '#888888';
    ctx.font = '18px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Chart preview', area.left + area.width / 2, area.top + area.height / 2);
  }
}

function drawTitleBar(ctx, panel) {
  const titleHeight = 100;
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
    drawTitleBar(ctx, panel);
  } else if (!toDataUri(panel.image_b64)) {
    drawMockChart(ctx, panel);
    drawTitleBar(ctx, panel);
  } else {
    ctx.fillStyle = '#555555';
    ctx.font = '18px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Loading chart…', 256, 260);
    drawTitleBar(ctx, panel);
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
