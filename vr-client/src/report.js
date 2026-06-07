import * as THREE from 'three';
import { scene } from './scene.js';

let reportGroup = null;

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

function sectionText(section) {
  if (typeof section === 'string') return section;
  if (section?.text) return section.text;
  if (section?.body) return section.body;
  if (section?.summary) return section.summary;
  const title = section?.title ?? '';
  const detail = section?.detail ?? section?.content ?? '';
  return [title, detail].filter(Boolean).join('\n');
}

function sectionTitle(section, index) {
  if (typeof section === 'string') return `Section ${index + 1}`;
  return section?.title ?? section?.heading ?? `Section ${index + 1}`;
}

function createSectionTexture(title, body) {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 512;
  const ctx = canvas.getContext('2d');

  ctx.fillStyle = '#141414';
  ctx.fillRect(0, 0, 512, 512);
  ctx.strokeStyle = '#555555';
  ctx.lineWidth = 2;
  ctx.strokeRect(2, 2, 508, 508);

  ctx.fillStyle = '#ffffff';
  ctx.font = 'bold 26px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.fillText(title, 256, 24);

  ctx.fillStyle = '#cccccc';
  ctx.font = '18px sans-serif';
  const lines = body.split('\n');
  let y = 80;
  lines.forEach((line) => {
    const words = line.split(' ');
    let row = '';
    words.forEach((word) => {
      const test = row ? `${row} ${word}` : word;
      if (ctx.measureText(test).width > 460) {
        ctx.fillText(row, 256, y);
        y += 24;
        row = word;
      } else {
        row = test;
      }
    });
    if (row) {
      ctx.fillText(row, 256, y);
      y += 24;
    }
  });

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

export function renderReport(msg) {
  clearReport();

  const sections = msg.sections ?? [];
  if (!sections.length) return;

  reportGroup = new THREE.Group();
  reportGroup.position.set(0, 1.6, -3.5);

  const total = sections.length;
  sections.forEach((section, i) => {
    const title = sectionTitle(section, i);
    const body = sectionText(section);
    const texture = createSectionTexture(title, body);
    const material = new THREE.MeshBasicMaterial({ map: texture, side: THREE.DoubleSide });
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(0.85, 0.85), material);

    const angle = (i - (total - 1) / 2) * 0.35;
    mesh.position.set(Math.sin(angle) * 1.8, 0, Math.cos(angle) * 0.3);
    mesh.lookAt(0, 0, 0.5);

    reportGroup.add(mesh);
  });

  scene.add(reportGroup);
}

export function clearReport() {
  if (reportGroup) {
    scene.remove(reportGroup);
    reportGroup.traverse(disposeObject);
  }
  reportGroup = null;
}