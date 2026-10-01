import * as THREE from 'three';
import { OrbitControls } from './vendor/three/examples/jsm/controls/OrbitControls.js';
import { TransformControls } from './vendor/three/examples/jsm/controls/TransformControls.js';
import { nextHit } from './catalog.mjs';

const fmt = value => Number(value.toFixed(2)).toString();

export class SpatialViewport {
  constructor(canvas, annotations, callbacks) {
    this.canvas = canvas;
    this.annotations = annotations;
    this.callbacks = callbacks;
    this.world = new THREE.Scene();
    this.world.background = new THREE.Color(0x101b25);
    this.perspective = new THREE.PerspectiveCamera(42, 1, .01, 100000);
    this.orthographic = new THREE.OrthographicCamera(-10, 10, 10, -10, .01, 100000);
    this.camera = this.perspective;
    this.camera.up.set(0, 0, 1);
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.45;
    this.orbit = new OrbitControls(this.camera, canvas);
    this.orbit.enableDamping = false;
    this.orbit.touches.ONE = THREE.TOUCH.PAN;
    this.orbit.touches.TWO = THREE.TOUCH.DOLLY_PAN;
    this.orbit.addEventListener('change', () => this.request());
    this.world.add(new THREE.HemisphereLight(0xd9fff1, 0x223a49, 2.4));
    const key = new THREE.DirectionalLight(0xe5fff8, 2.6);
    key.position.set(2, -3, 5);
    this.world.add(key);
    const rim = new THREE.DirectionalLight(0x668db3, 1.7);
    rim.position.set(-3, 2, 3);
    this.world.add(rim);
    this.environment = new THREE.Group();
    this.cells = new THREE.Group();
    this.field = new THREE.Group();
    this.blocks = new THREE.Group();
    this.objects = new THREE.Group();
    this.measureGroup = new THREE.Group();
    this.measurePoints = [];
    this.world.add(this.environment, this.field, this.cells, this.blocks, this.objects, this.measureGroup);
    // Gizmo edits a block mesh live; the block itself is only updated once the drag ends.
    this.transform = new TransformControls(this.camera, canvas);
    this.transform.addEventListener('change', () => this.request());
    this.transform.addEventListener('dragging-changed', event => {
      this.orbit.enabled = !event.value;
      if (!event.value) { this.justTransformed = true; this.commitTransform(); }
    });
    this.world.add(this.transform.getHelper());
    this.geometryCache = new Map();
    this.material = new THREE.MeshStandardMaterial({ color: 0x63d8bc, roughness: .38, metalness: .08,
      transparent: false, opacity: 1, depthWrite: true });
    this.selectedMaterial = new THREE.MeshStandardMaterial({ color: 0xffc481, emissive: 0x7a4d20,
      emissiveIntensity: .25, roughness: .3, transparent: true, opacity: .94, depthWrite: false });
    this.unknownMaterial = new THREE.MeshBasicMaterial({ color: 0xb3cbd0, wireframe: true });
    this.raycaster = new THREE.Raycaster();
    this.domainKey = '';
    this.selectedId = null;
    this.view = 'perspective';
    this.mode = 'space';
    this.tool = 'select';
    this.pending = false;
    this.pointers = new Set();
    canvas.addEventListener('pointerdown', event => {
      this.pointers.add(event.pointerId);
      if (this.pointers.size === 1) { this.multiGesture = false; this.pointerStart = { x: event.clientX, y: event.clientY }; }
      else this.multiGesture = true;
    });
    canvas.addEventListener('pointercancel', event => { this.pointers.delete(event.pointerId); this.pointerStart = null; });
    canvas.addEventListener('pointerup', event => {
      this.pointers.delete(event.pointerId);
      const transformed = this.justTransformed || this.transform.dragging || this.transform.axis;
      this.justTransformed = false;
      if (transformed || this.multiGesture || event.button !== 0 || !this.pointerStart || Math.hypot(event.clientX - this.pointerStart.x,
        event.clientY - this.pointerStart.y) > 5) return;
      this.pick(event);
    });
    canvas.addEventListener('dragover', event => { if (this.mode === 'space') event.preventDefault(); });
    canvas.addEventListener('drop', event => {
      event.preventDefault();
      const kind = event.dataTransfer.getData('application/friskoli-object');
      if (this.mode !== 'space' || !['population','obstacle_box','local_source','degradable_box'].includes(kind) || !this.size) return;
      this.pointFromEvent(event);
      const point = this.raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0,0,1), -this.size[2]/2), new THREE.Vector3());
      if (point) (kind === 'population' ? this.callbacks.placePopulation : this.callbacks.placeEnvironment)?.(point.toArray());
    });
    canvas.addEventListener('contextmenu', event => {
      event.preventDefault();
      if (this.mode !== 'space' || !this.pointerStart ||
          Math.hypot(event.clientX - this.pointerStart.x, event.clientY - this.pointerStart.y) > 5) return;
      const id = this.hitBlock(event);
      if (id) this.callbacks.contextBlock(id, event.clientX, event.clientY);
    });
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(canvas.parentElement);
  }

  clear(group) {
    for (const child of [...group.children]) {
      group.remove(child);
      child.traverse(object => {
        if (object.geometry && !this.geometryCacheHas(object.geometry)) object.geometry.dispose();
        object.userData.texture?.dispose();
        if (object.material && object.material !== this.material && object.material !== this.selectedMaterial &&
            object.material !== this.unknownMaterial) object.material.dispose();
      });
    }
  }

  geometryCacheHas(geometry) { return [...this.geometryCache.values()].includes(geometry); }

  setDomain(domain) {
    const key = JSON.stringify(domain);
    if (key === this.domainKey) return;
    this.domainKey = key;
    this.domain = domain;
    this.clear(this.environment);
    const size = domain.counts_xyz.map((count, axis) => count * domain.spacing_um_xyz[axis]);
    this.size = size;
    const box = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(...size)),
      new THREE.LineBasicMaterial({ color: 0x718e9c, transparent: true, opacity: .54 }));
    box.position.set(size[0] / 2, size[1] / 2, size[2] / 2);
    this.environment.add(box);
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(size[0], size[1]),
      new THREE.MeshBasicMaterial({ color: 0x1b313c, side: THREE.DoubleSide,
        transparent: true, opacity: .25, depthWrite: false }));
    floor.position.set(size[0] / 2, size[1] / 2, 0);
    this.environment.add(floor);
    const grid = new THREE.GridHelper(Math.max(size[0], size[1]), Math.min(24, Math.max(domain.counts_xyz[0], domain.counts_xyz[1])),
      0x41616e, 0x2b4550);
    grid.rotation.x = Math.PI / 2;
    grid.position.set(size[0] / 2, size[1] / 2, .003);
    grid.material.transparent = true;
    grid.material.opacity = .48;
    this.environment.add(grid);
    this.grid = grid;
    const axes = new THREE.AxesHelper(Math.max(2, Math.min(...size) * .22));
    axes.position.set(0, 0, .02);
    this.environment.add(axes);
    this.fit();
  }

  geometryFor(cell) {
    if (!cell.geometry) {
      const radius = Math.min(...this.domain.spacing_um_xyz) * .2, key = `unknown:${radius}`;
      if (!this.geometryCache.has(key)) this.geometryCache.set(key, new THREE.SphereGeometry(radius, 8, 6));
      return this.geometryCache.get(key);
    }
    const { length_um: length, diameter_um: diameter } = cell.geometry;
    const key = `${length}:${diameter}`;
    if (!this.geometryCache.has(key)) {
      const geometry = new THREE.CapsuleGeometry(diameter / 2, Math.max(0, length - diameter), 5, 12);
      geometry.rotateZ(-Math.PI / 2);
      this.geometryCache.set(key, geometry);
    }
    return this.geometryCache.get(key);
  }

  setSnapshot(snapshot, selectedId, fieldId = '', slice = 0, range = null) {
    this.snapshot = snapshot;
    this.selectedId = selectedId;
    this.clear(this.cells);
    this.clear(this.field);
    this.meshes = [];
    const batches = new Map();
    for (const cell of snapshot.frame.cells) {
      const key = `${cell.geometry?.length_um}:${cell.geometry?.diameter_um}:${cell.id === selectedId}`;
      if (!batches.has(key)) batches.set(key, []); batches.get(key).push(cell);
    }
    const pose = new THREE.Object3D();
    for (const cells of batches.values()) {
      const first = cells[0];
      const mesh = new THREE.InstancedMesh(this.geometryFor(first), !first.geometry ? this.unknownMaterial :
        first.id === selectedId ? this.selectedMaterial : this.material, cells.length);
      cells.forEach((cell, index) => {
        pose.position.fromArray(cell.position_um); pose.quaternion.fromArray(cell.orientation_xyzw); pose.updateMatrix(); mesh.setMatrixAt(index, pose.matrix);
      });
      mesh.instanceMatrix.needsUpdate = true;
      mesh.userData.cellIds = cells.map(cell => cell.id);
      mesh.renderOrder = first.id === selectedId ? 3 : 2;
      this.cells.add(mesh);
      this.meshes.push(mesh);
    }
    const field = snapshot.concentrations[fieldId];
    if (field && range) {
      const values = field.values_zyx[slice];
      const [nx, ny] = this.domain.counts_xyz;
      const paint = document.createElement('canvas');
      paint.width = nx;
      paint.height = ny;
      const ctx = paint.getContext('2d');
      const pixels = ctx.createImageData(nx, ny);
      for (let y = 0; y < ny; y++) for (let x = 0; x < nx; x++) {
        const scale = range.max > range.min ? (values[y][x] - range.min) / (range.max - range.min) : .5;
        const offset = ((ny - y - 1) * nx + x) * 4;
        pixels.data[offset] = 28 + scale * 72;
        pixels.data[offset + 1] = 72 + scale * 140;
        pixels.data[offset + 2] = 92 + scale * 98;
        pixels.data[offset + 3] = 215;
      }
      ctx.putImageData(pixels, 0, 0);
      const texture = new THREE.CanvasTexture(paint);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.magFilter = THREE.NearestFilter;
      texture.minFilter = THREE.NearestFilter;
      const plane = new THREE.Mesh(new THREE.PlaneGeometry(this.size[0], this.size[1]),
        new THREE.MeshBasicMaterial({ map: texture, transparent: true, opacity: .47,
          side: THREE.DoubleSide, depthWrite: false }));
      plane.position.set(this.size[0] / 2, this.size[1] / 2,
        (slice + .5) * this.domain.spacing_um_xyz[2]);
      plane.userData.texture = texture;
      plane.raycast = () => {};
      this.field.add(plane);
    }
    this.request();
  }

  setBlocks(blocks, selectedBlock) {
    this.transform.detach();
    this.clear(this.blocks);
    this.blockHits = [];
    this.blockData = blocks;
    this.selectedBlock = selectedBlock;
    for (const block of blocks) {
      if (block.hidden || (!block.dirty && block.id !== selectedBlock)) continue;
      const geometry = new THREE.BoxGeometry(...block.size);
      const material = new THREE.MeshBasicMaterial({ color: block.id === selectedBlock ? 0xf3ba78 : 0x4dcaaf,
        transparent: true, opacity: .045, depthWrite: false, side: THREE.DoubleSide });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.fromArray(block.center);
      mesh.rotation.set(...(block.rotation ?? [0,0,0]).map(n => n * Math.PI / 180));
      mesh.userData.blockId = block.id;
      const outline = new THREE.LineSegments(new THREE.EdgesGeometry(geometry),
        new THREE.LineBasicMaterial({ color: block.id === selectedBlock ? 0xf3ba78 : 0x59bda9,
          transparent: true, opacity: block.id === selectedBlock ? .9 : .43 }));
      mesh.add(outline);
      this.blocks.add(mesh);
      if (block.dirty || this.tool === 'move' || this.tool === 'scale' || this.tool === 'rotate') this.blockHits.push(mesh);
    }
    this.blocks.visible = this.mode === 'space';
    this.attachGizmo();
    this.request();
  }

  // Shapes are derived from the same graph parameters submitted to the solver.
  // Source spheres mark release support, never a fabricated concentration field.
  setObjects(objects, selectedId) {
    this.clear(this.objects);
    this.objectHits = [];
    for (const object of objects) {
      if (object.kind === 'degradable_box' && object.remaining_molecules === 0) continue;
      const box = object.kind !== 'local_source';
      const size = box ? object.upper.map((value,i) => value-object.lower[i]) : null;
      if (box ? !size.every(value => Number.isFinite(value) && value > 0) :
        !object.center.every(Number.isFinite) || !Number.isFinite(object.radius) || object.radius <= 0) continue;
      const geometry = box ? new THREE.BoxGeometry(...size) : new THREE.SphereGeometry(object.radius,20,12);
      const color = selectedId === object.id ? 0xffc481 : object.kind === 'degradable_box' ? 0xc59c65 : box ? 0x8094ac : 0x69b4ec;
      const mesh = new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color,
        transparent:true,opacity:box ? .6 : .14,roughness:.8,depthWrite:box,side:THREE.DoubleSide}));
      mesh.position.fromArray(box ? object.lower.map((value,i) => (value+object.upper[i])/2) : object.center);
      mesh.userData.nodeId = object.id;
      const outline = new THREE.LineSegments(new THREE.EdgesGeometry(geometry),new THREE.LineBasicMaterial({color,transparent:true,opacity:.75}));
      outline.raycast = () => {};
      mesh.add(outline);
      this.objects.add(mesh); this.objectHits.push(mesh);
    }
    this.request();
  }

  // Shows the move/scale gizmo on the selected block while a transform tool is active in Space.
  attachGizmo() {
    const mesh = this.blockHits?.find(item => item.userData.blockId === this.selectedBlock);
    const block = this.blockData?.find(item => item.id === this.selectedBlock);
    if (this.mode === 'space' && mesh && !block?.locked && ['move','rotate','scale'].includes(this.tool)) {
      this.transform.setMode(this.tool === 'move' ? 'translate' : this.tool);
      this.transform.showX = this.tool !== 'rotate' || this.domain.geometry !== 'thin_layer';
      this.transform.showY = this.tool !== 'rotate' || this.domain.geometry !== 'thin_layer';
      this.transform.showZ = !(this.domain.geometry === 'thin_layer' && this.tool !== 'rotate');
      this.transform.attach(mesh);
    } else this.transform.detach();
  }

  commitTransform() {
    const mesh = this.transform.object;
    const block = this.blockData?.find(item => item.id === mesh?.userData.blockId);
    if (!block) return;
    const size = block.size.map((value, axis) => value * Math.abs(mesh.scale.getComponent(axis)));
    this.callbacks.transformBlock(block.id, mesh.position.toArray(), size, [mesh.rotation.x,mesh.rotation.y,mesh.rotation.z].map(v => v * 180 / Math.PI));
  }

  setSnap(on) {
    const step = on && this.domain ? Math.min(...this.domain.spacing_um_xyz) : null;
    this.transform.setTranslationSnap(step);
    this.transform.setScaleSnap(on ? .1 : null);
    this.transform.setRotationSnap(on ? Math.PI / 36 : null);
  }

  setMode(mode) {
    this.mode = mode;
    this.blocks.visible = mode === 'space';
    this.attachGizmo();
    if (mode !== 'space') this.clearMeasure();
    this.request();
  }

  setTool(tool) {
    this.tool = tool;
    this.canvas.style.cursor = ['population','environment','measure'].includes(tool) ? 'crosshair' : 'default';
    this.orbit.mouseButtons.LEFT = tool === 'hand' ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
    this.orbit.touches.ONE = tool === 'orbit' ? THREE.TOUCH.ROTATE : THREE.TOUCH.PAN;
    if (tool !== 'measure') this.clearMeasure();
    this.attachGizmo();
    this.request();
  }

  toggleGrid() { if (this.grid) { this.grid.visible = !this.grid.visible; this.request(); } }

  clearMeasure() {
    this.measurePoints = [];
    this.clear(this.measureGroup);
    this.callbacks.measure?.(null);
  }

  // Measures on the mid-depth plane of the domain; a third click starts a new measurement.
  addMeasurePoint() {
    if (!this.size) return;
    const normal = this.camera.getWorldDirection(new THREE.Vector3());
    const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(normal, new THREE.Vector3(...this.size.map(v => v/2)));
    const point = this.raycaster.ray.intersectPlane(plane, new THREE.Vector3());
    if (!point) return;
    if (this.measurePoints.length >= 2) this.clearMeasure();
    this.measurePoints.push(point);
    this.clear(this.measureGroup);
    const radius = Math.min(...this.domain.spacing_um_xyz) * .18;
    for (const p of this.measurePoints) {
      const dot = new THREE.Mesh(new THREE.SphereGeometry(radius, 10, 8), new THREE.MeshBasicMaterial({ color: 0xf1bb7b }));
      dot.position.copy(p);
      this.measureGroup.add(dot);
    }
    if (this.measurePoints.length === 2) {
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(this.measurePoints),
        new THREE.LineBasicMaterial({ color: 0xf1bb7b }));
      this.measureGroup.add(line);
      const [a, b] = this.measurePoints;
      this.callbacks.measure?.({ distance: a.distanceTo(b), delta: b.clone().sub(a).toArray() });
    }
    this.request();
  }

  pointFromEvent(event) {
    const rect = this.canvas.getBoundingClientRect();
    this.raycaster.setFromCamera(new THREE.Vector2(
      (event.clientX - rect.left) / rect.width * 2 - 1,
      -(event.clientY - rect.top) / rect.height * 2 + 1), this.camera);
  }

  hitBlock(event) {
    this.pointFromEvent(event);
    return this.raycaster.intersectObjects(this.blockHits ?? [])[0]?.object.userData.blockId ?? null;
  }

  pick(event) {
    this.pointFromEvent(event);
    if (this.mode === 'space') {
      if (this.tool === 'measure') { this.addMeasurePoint(); return; }
      if (['population','environment'].includes(this.tool)) {
        const point = this.raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1),
          -this.size[2] / 2), new THREE.Vector3());
        if (point) (this.tool === 'population' ? this.callbacks.placePopulation : this.callbacks.placeEnvironment)?.(point.toArray());
        return;
      }
      const objectId = this.raycaster.intersectObjects(this.objectHits ?? [],false)[0]?.object.userData.nodeId;
      if (objectId) { this.callbacks.selectEnvironment?.(objectId); return; }
      const id = this.raycaster.intersectObjects(this.blockHits ?? [])[0]?.object.userData.blockId;
      if (id) this.callbacks.selectBlock(id);
      return;
    }
    const objectId = this.raycaster.intersectObjects(this.objectHits ?? [],false)[0]?.object.userData.nodeId;
    if (objectId) { this.callbacks.selectEnvironment?.(objectId); return; }
    if (!this.meshes?.length) return;
    const ids = [...new Set(this.raycaster.intersectObjects(this.meshes).map(hit => hit.object.userData.cellIds[hit.instanceId]))];
    if (ids.length) this.callbacks.selectCell(nextHit(ids, this.selectedId));
  }

  setCamera(view) {
    if (!this.size) return;
    this.view = view;
    const center = new THREE.Vector3(...this.size.map(value => value / 2));
    const radius = new THREE.Vector3(...this.size).length() / 2;
    const aspect = this.canvas.clientWidth / Math.max(1, this.canvas.clientHeight);
    const height = Math.max(radius * 2.5 / Math.min(1, aspect), 2);
    this.orthographic.left = -height * aspect / 2;
    this.orthographic.right = height * aspect / 2;
    this.orthographic.top = height / 2;
    this.orthographic.bottom = -height / 2;
    this.orthographic.updateProjectionMatrix();
    this.camera = view === 'perspective' ? this.perspective : this.orthographic;
    this.orthographic.zoom = 1;
    const direction = ({ top: [0, 0, 1], front: [0, -1, 0], right: [1, 0, 0],
      orthographic: [1, -1.25, .9], perspective: [1, -1.25, .9] })[view] ?? [1, -1.25, .9];
    const halfFov = Math.atan(Math.tan(THREE.MathUtils.degToRad(this.perspective.fov / 2)) * Math.min(1, aspect));
    const distance = radius / Math.sin(halfFov) * 1.12;
    this.camera.position.copy(center).add(new THREE.Vector3(...direction).normalize().multiplyScalar(distance));
    this.lastAspect = aspect;
    this.camera.up.set(0, view === 'top' ? 1 : 0, view === 'top' ? 0 : 1);
    this.camera.lookAt(center);
    this.orbit.object = this.camera;
    this.transform.camera = this.camera;
    this.orbit.target.copy(center);
    this.orbit.update();
    this.resize();
  }

  fit() { if (this.size) this.setCamera(this.view); }

  zoom(factor) {
    if (this.camera.isOrthographicCamera) {
      this.camera.zoom = Math.max(.1, Math.min(20, this.camera.zoom * factor));
      this.camera.updateProjectionMatrix();
    } else this.camera.position.sub(this.orbit.target).multiplyScalar(1 / factor).add(this.orbit.target);
    this.orbit.update();
    this.request();
  }

  resize() {
    const { width, height } = this.canvas.getBoundingClientRect();
    if (width < 1 || height < 1) return;
    const aspect = width / height;
    if (this.lastAspect && this.camera.isPerspectiveCamera) {
      this.camera.position.sub(this.orbit.target).multiplyScalar(Math.min(1, this.lastAspect) / Math.min(1, aspect)).add(this.orbit.target);
    }
    this.lastAspect = aspect;
    this.renderer.setSize(width, height, false);
    this.perspective.aspect = width / height;
    this.perspective.updateProjectionMatrix();
    if (this.size && this.camera.isOrthographicCamera) {
      const span = this.orthographic.top - this.orthographic.bottom;
      this.orthographic.left = -span * width / height / 2;
      this.orthographic.right = span * width / height / 2;
      this.orthographic.updateProjectionMatrix();
    }
    this.request();
  }

  request() {
    if (this.pending) return;
    this.pending = true;
    requestAnimationFrame(() => {
      this.pending = false;
      if (this.canvas.clientWidth < 1) return;
      this.renderer.render(this.world, this.camera);
      this.drawAnnotations();
    });
  }

  drawAnnotations() {
    this.annotations.replaceChildren();
    if (!this.snapshot) return;
    if (this.mode === 'space') return;
    const groups = new Map();
    for (const cell of this.snapshot.frame.cells) {
      const key = cell.position_um.map(value => value.toFixed(4)).join(':');
      const group = groups.get(key) ?? [];
      group.push(cell);
      groups.set(key, group);
    }
    const marker = (position, text, className, ids = []) => {
      const projected = new THREE.Vector3(...position).project(this.camera);
      if (projected.z < -1 || projected.z > 1 || Math.abs(projected.x) > 1 || Math.abs(projected.y) > 1) return;
      const label = document.createElement('button');
      label.type = 'button';
      label.className = `scene-label ${className}`;
      label.textContent = text;
      label.style.left = `${(projected.x + 1) * this.canvas.clientWidth / 2}px`;
      label.style.top = `${(1 - projected.y) * this.canvas.clientHeight / 2}px`;
      if (ids.length) label.addEventListener('click', () => this.callbacks.selectCell(nextHit(ids, this.selectedId)));
      this.annotations.append(label);
    };
    for (const cells of groups.values()) {
      if (cells.length > 1) marker(cells[0].position_um, `${cells.length} cells`, 'cluster', cells.map(cell => cell.id));
    }
    const selected = this.snapshot.frame.cells.find(cell => cell.id === this.selectedId);
    if (selected) marker(selected.position_um, selected.id, 'selected', [selected.id]);
  }
}
