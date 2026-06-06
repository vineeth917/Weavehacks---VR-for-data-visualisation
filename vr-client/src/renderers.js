import * as THREE from 'three';
import { scene } from './scene.js';

let kdeMesh = null;
let corrGroup = null;

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

function disposeGroup(group) {
  if (!group) return;

  scene.remove(group);
  group.traverse((obj) => {
    disposeObject(obj);
  });
}

export function renderKDESurface(msg) {
  if (kdeMesh) {
    scene.remove(kdeMesh);
    disposeObject(kdeMesh);
    kdeMesh = null;
  }

  const grid = msg.grid;
  const rows = grid.length;
  const cols = grid[0].length;

  const geometry = new THREE.PlaneGeometry(3, 3, cols - 1, rows - 1);
  const position = geometry.attributes.position;

  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const index = row * cols + col;
      position.setY(index, grid[row][col] * 2);
    }
  }

  position.needsUpdate = true;
  geometry.computeVertexNormals();

  const material = new THREE.MeshStandardMaterial({
    color: 0x1d9e75,
    wireframe: false,
    side: THREE.DoubleSide,
  });

  kdeMesh = new THREE.Mesh(geometry, material);
  kdeMesh.position.set(2, 1.0, -2);
  kdeMesh.rotation.x = -Math.PI / 2;

  scene.add(kdeMesh);
}

export function renderCorrField(msg) {
  if (corrGroup) {
    disposeGroup(corrGroup);
    corrGroup = null;
  }

  const { labels, matrix } = msg;
  corrGroup = new THREE.Group();

  for (let i = 0; i < matrix.length; i++) {
    for (let j = 0; j < matrix[i].length; j++) {
      const value = matrix[i][j];
      const height = Math.abs(value) * 1.5;
      const color = value > 0 ? 0x3498db : 0xe74c3c;

      const geometry = new THREE.BoxGeometry(0.08, height, 0.08);
      const material = new THREE.MeshStandardMaterial({ color });
      const bar = new THREE.Mesh(geometry, material);

      bar.position.set(
        i * 0.12 - labels.length * 0.06,
        height / 2,
        j * 0.12
      );

      corrGroup.add(bar);
    }
  }

  corrGroup.position.set(3, 1.0, -1);
  scene.add(corrGroup);
}

export function clearRenderers() {
  if (kdeMesh) {
    scene.remove(kdeMesh);
    disposeObject(kdeMesh);
    kdeMesh = null;
  }

  if (corrGroup) {
    disposeGroup(corrGroup);
    corrGroup = null;
  }
}
