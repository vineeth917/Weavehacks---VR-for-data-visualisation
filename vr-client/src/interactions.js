import * as THREE from 'three';
import { sendInteraction } from './ws.js';
import { getNearbyPointIds } from './scatter3d.js';
import { startListening } from './voice.js';

const GRAB_RADIUS = 0.35;
const VR_HIT_SCALE = 2.2;
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
const rayOrigin = new THREE.Vector3();
const rayDirection = new THREE.Vector3();
const poseQuaternion = new THREE.Quaternion();
const poseMatrix = new THREE.Matrix4();

let sceneRef = null;
let cameraRef = null;
let rendererRef = null;
let uiHandler = null;
let initialized = false;
let vrMode = false;
let lastHover = null;
let wasPinching = false;
const indexTipPos = new THREE.Vector3();
const thumbTipPos = new THREE.Vector3();

const controllers = [];
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

function pickHit(intersects) {
  if (!intersects.length) return null;
  const dataHit = intersects.find((hit) => hit.object.userData.kind !== 'ui');
  return dataHit ?? intersects[0];
}

function pulseHit(mesh) {
  if (!mesh) return;
  const base = mesh.userData.baseScale ?? 1;
  mesh.scale.set(base * 1.3, base * 1.3, base * 1.3);
  setTimeout(() => {
    mesh.scale.set(base, base, base);
  }, 200);
}

function handleSelect(intersect) {
  const mesh = intersect.object;
  const { kind, id, action } = mesh.userData;

  if (kind === 'ui') {
    uiHandler?.(action);
    console.log('VR UI:', action);
    return;
  }

  if (!id) return;

  pulseHit(mesh);
  sendInteraction('select_point', { target_id: id });
  console.log('Selected:', { kind, id });
}

function handleGrab(intersect) {
  const mesh = intersect.object;
  const { kind, id } = mesh.userData;

  if (kind === 'ui') return;

  pulseHit(mesh);

  if (kind === 'point') {
    const worldPos = new THREE.Vector3();
    mesh.getWorldPosition(worldPos);
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

function raycastFromRay(origin, direction) {
  raycaster.ray.origin.copy(origin);
  raycaster.ray.direction.copy(direction).normalize();
  raycaster.far = 25;
  raycaster.near = 0.01;
  return raycaster.intersectObjects(getInteractableMeshes(), false);
}

function raycastFromCamera(clientX, clientY) {
  mouse.x = (clientX / window.innerWidth) * 2 - 1;
  mouse.y = -(clientY / window.innerHeight) * 2 + 1;
  raycaster.setFromCamera(mouse, cameraRef);
  raycaster.far = 25;
  return raycaster.intersectObjects(getInteractableMeshes(), false);
}

function raycastFromController(controller) {
  const matrix = new THREE.Matrix4();
  matrix.identity().extractRotation(controller.matrixWorld);

  rayOrigin.setFromMatrixPosition(controller.matrixWorld);
  rayDirection.set(0, 0, -1).applyMatrix4(matrix);

  return raycastFromRay(rayOrigin, rayDirection);
}

function raycastFromXRFrame(frame, inputSource, referenceSpace) {
  if (!frame || !inputSource?.targetRaySpace || !referenceSpace) return [];

  const pose = frame.getPose(inputSource.targetRaySpace, referenceSpace);
  if (!pose) return [];

  poseQuaternion.set(
    pose.transform.orientation.x,
    pose.transform.orientation.y,
    pose.transform.orientation.z,
    pose.transform.orientation.w
  );
  rayOrigin.set(
    pose.transform.position.x,
    pose.transform.position.y,
    pose.transform.position.z
  );
  poseMatrix.makeRotationFromQuaternion(poseQuaternion);
  rayDirection.set(0, 0, -1).applyMatrix4(poseMatrix);

  return raycastFromRay(rayOrigin, rayDirection);
}

function setHover(mesh) {
  if (lastHover && lastHover !== mesh) {
    const base = lastHover.userData.baseScale ?? 1;
    lastHover.scale.set(base, base, base);
  }

  lastHover = mesh ?? null;

  if (lastHover) {
    const base = lastHover.userData.baseScale ?? 1;
    lastHover.scale.set(base * 1.12, base * 1.12, base * 1.12);
  }

  controllerRays.forEach((ray) => {
    ray.material.color.set(mesh ? 0x44ff88 : 0xffffff);
  });
}

function onMouseClick(event) {
  const intersects = raycastFromCamera(event.clientX, event.clientY);
  const hit = pickHit(intersects);
  if (!hit) return;

  if (event.shiftKey) {
    handleGrab(hit);
  } else {
    handleSelect(hit);
  }
}

function onSessionSelect(event) {
  const referenceSpace = rendererRef.xr.getReferenceSpace();
  const intersects = raycastFromXRFrame(event.frame, event.inputSource, referenceSpace);
  const hit = pickHit(intersects);

  if (!hit) {
    console.log('VR select: no target (meshes in scene:', getInteractableMeshes().length, ')');
    return;
  }

  handleSelect(hit);
}

function onSessionSqueeze(event) {
  const referenceSpace = rendererRef.xr.getReferenceSpace();
  const intersects = raycastFromXRFrame(event.frame, event.inputSource, referenceSpace);
  const hit = pickHit(intersects);

  if (!hit) {
    console.log('VR squeeze: no target');
    return;
  }

  handleGrab(hit);
}

function onXRSessionStart() {
  vrMode = true;
  enlargeHitTargets(true);

  const session = rendererRef.xr.getSession();
  if (!session) return;

  session.addEventListener('select', onSessionSelect);
  session.addEventListener('squeeze', onSessionSqueeze);
  console.log('VR interaction listeners attached, interactables:', getInteractableMeshes().length);
}

function onXRSessionEnd() {
  vrMode = false;
  enlargeHitTargets(false);
  setHover(null);
}

function enlargeHitTargets(enlarge) {
  if (!sceneRef) return;

  sceneRef.traverse((obj) => {
    if (!obj.isMesh || !obj.userData.interactable) return;
    if (!['point', 'corr'].includes(obj.userData.kind)) return;

    if (obj.userData.baseScale === undefined) {
      obj.userData.baseScale = obj.scale.x;
    }

    const base = obj.userData.baseScale;
    const factor = enlarge ? VR_HIT_SCALE : 1;
    obj.scale.set(base * factor, base * factor, base * factor);
  });
}

export function notifySceneObjectsChanged() {
  if (vrMode) enlargeHitTargets(true);
}

function setupXRController(index) {
  const controller = rendererRef.xr.getController(index);

  const rayGeometry = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, 0, 0),
    new THREE.Vector3(0, 0, -1.5),
  ]);
  const ray = new THREE.Line(
    rayGeometry,
    new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.85 })
  );
  controller.add(ray);
  controllerRays.push(ray);
  controllers.push(controller);

  sceneRef.add(controller);
}

export function updateXRInteractions() {
  if (!vrMode || !rendererRef?.xr?.isPresenting || controllers.length === 0) return;

  let closest = null;
  let closestDist = Infinity;

  for (const controller of controllers) {
    controller.updateMatrixWorld(true);
    const intersects = raycastFromController(controller);
    const hit = pickHit(intersects);
    if (hit && hit.distance < closestDist) {
      closest = hit.object;
      closestDist = hit.distance;
    }
  }

  setHover(closest);

  const frame = rendererRef.xr.getFrame();
  const referenceSpace = rendererRef.xr.getReferenceSpace();
  const session = rendererRef.xr.getSession();
  if (!frame || !referenceSpace || !session) return;

  let isPinching = false;

  for (const inputSource of session.inputSources) {
    if (inputSource.handedness !== 'right' || !inputSource.hand) continue;

    const indexJoint = inputSource.hand.get('index-finger-tip');
    const thumbJoint = inputSource.hand.get('thumb-tip');
    if (!indexJoint || !thumbJoint) continue;

    const indexPose = frame.getJointPose(indexJoint, referenceSpace);
    const thumbPose = frame.getJointPose(thumbJoint, referenceSpace);
    if (!indexPose || !thumbPose) continue;

    indexTipPos.set(
      indexPose.transform.position.x,
      indexPose.transform.position.y,
      indexPose.transform.position.z
    );
    thumbTipPos.set(
      thumbPose.transform.position.x,
      thumbPose.transform.position.y,
      thumbPose.transform.position.z
    );

    if (indexTipPos.distanceTo(thumbTipPos) < 0.03) {
      isPinching = true;
    }
    break;
  }

  if (isPinching && !wasPinching) {
    console.log('Pinch detected - starting voice input');
    startListening();
  }

  wasPinching = isPinching;
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

  renderer.xr.addEventListener('sessionstart', onXRSessionStart);
  renderer.xr.addEventListener('sessionend', onXRSessionEnd);
}
