import * as THREE from 'three';
import { sendInteraction } from './ws.js';
import { getNearbyPointIds } from './scatter3d.js';

const GRAB_RADIUS = 0.35;
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();

let sceneRef = null;
let cameraRef = null;
let rendererRef = null;
let uiHandler = null;
let initialized = false;
const controllerRays = [];

function getInteractableMeshes() {
  if (!sceneRef) return [];

  const meshes = [];
  sceneRef.traverse((obj) => {
    if (obj.isMesh && obj.userData.interactable) {
      meshes.push(obj);
    }
  });
  return meshes;
}

function handleSelect(intersect) {
  const { kind, id, action } = intersect.object.userData;

  if (kind === 'ui') {
    uiHandler?.(action);
    return;
  }

  if (!id) return;

  sendInteraction('select_point', { target_id: id });
  console.log('Selected:', { kind, id });
}

function handleGrab(intersect) {
  const { kind, id } = intersect.object.userData;

  if (kind === 'ui') return;

  if (kind === 'point') {
    const worldPos = new THREE.Vector3();
    intersect.object.getWorldPosition(worldPos);
    const pointIds = getNearbyPointIds(worldPos, GRAB_RADIUS);

    sendInteraction('grab_region', { point_ids: pointIds.length ? pointIds : [id] });
    console.log('Grabbed region:', pointIds);
    return;
  }

  if (kind === 'panel' || kind === 'kde' || kind === 'corr') {
    sendInteraction('select_point', { target_id: id });
    console.log('Grabbed object:', { kind, id });
  }
}

function raycastFromCamera(clientX, clientY) {
  mouse.x = (clientX / window.innerWidth) * 2 - 1;
  mouse.y = -(clientY / window.innerHeight) * 2 + 1;

  raycaster.setFromCamera(mouse, cameraRef);
  return raycaster.intersectObjects(getInteractableMeshes(), false);
}

function raycastFromController(controller) {
  const matrix = new THREE.Matrix4();
  matrix.identity().extractRotation(controller.matrixWorld);

  raycaster.ray.origin.setFromMatrixPosition(controller.matrixWorld);
  raycaster.ray.direction.set(0, 0, -1).applyMatrix4(matrix);

  return raycaster.intersectObjects(getInteractableMeshes(), false);
}

function onControllerAction(controller, handler) {
  const intersects = raycastFromController(controller);
  if (intersects.length > 0) {
    handler(intersects[0]);
  }
}

function onMouseClick(event) {
  const intersects = raycastFromCamera(event.clientX, event.clientY);
  if (intersects.length === 0) return;

  if (event.shiftKey) {
    handleGrab(intersects[0]);
  } else {
    handleSelect(intersects[0]);
  }
}

function setupXRController(index) {
  const controller = rendererRef.xr.getController(index);

  const rayGeometry = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, 0, 0),
    new THREE.Vector3(0, 0, -4),
  ]);
  const ray = new THREE.Line(
    rayGeometry,
    new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.65 })
  );
  controller.add(ray);
  controllerRays.push(ray);

  const onSelect = () => onControllerAction(controller, handleSelect);
  const onSqueeze = () => onControllerAction(controller, handleGrab);

  controller.addEventListener('select', onSelect);
  controller.addEventListener('selectstart', onSelect);
  controller.addEventListener('squeeze', onSqueeze);
  controller.addEventListener('squeezestart', onSqueeze);

  sceneRef.add(controller);
}

export function initInteractions(scene, camera, renderer, onUIAction) {
  if (initialized) return;

  sceneRef = scene;
  cameraRef = camera;
  rendererRef = renderer;
  uiHandler = onUIAction;
  initialized = true;

  renderer.domElement.addEventListener('click', onMouseClick);
  setupXRController(0);
  setupXRController(1);
}
