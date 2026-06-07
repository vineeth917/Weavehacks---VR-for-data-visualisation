import { getPanelMesh } from './panels.js';
import { getPointMesh } from './scatter3d.js';

export function highlightTargets(targetIds) {
  const ids = Array.isArray(targetIds) ? targetIds : [targetIds];

  ids.forEach((id) => {
    const panel = getPanelMesh(id);
    if (panel) {
      pulseScale(panel);
      return;
    }

    const point = getPointMesh(id);
    if (point) {
      pulseScale(point);
      pulseEmissive(point);
    }
  });
}

function pulseScale(mesh) {
  mesh.scale.set(1.15, 1.15, 1.15);
  setTimeout(() => {
    mesh.scale.set(1.0, 1.0, 1.0);
  }, 800);
}

function pulseEmissive(mesh) {
  if (!mesh.material?.emissive) return;

  const original = mesh.material.emissive.getHex();
  const originalIntensity = mesh.material.emissiveIntensity ?? 0;

  mesh.material.emissive.setHex(0xffffff);
  mesh.material.emissiveIntensity = 0.6;

  setTimeout(() => {
    mesh.material.emissive.setHex(original);
    mesh.material.emissiveIntensity = originalIntensity;
  }, 800);
}
