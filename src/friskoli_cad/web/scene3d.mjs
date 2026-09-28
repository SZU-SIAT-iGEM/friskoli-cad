import * as THREE from 'three';
import { OrbitControls } from './vendor/three/examples/jsm/controls/OrbitControls.js';
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
    this.world.add(this.environment, this.field, this.cells, this.blocks);
    this.geometryCache = new Map();
    this.material = new THREE.MeshStandardMaterial({ color: 0x63d8bc, roughness: .38, metalness: .08,
      transparent: true, opacity: .76, depthWrite: false });
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
    canvas.addEventListener('pointerdown', event => { this.pointerStart = { x: event.clientX, y: event.clientY }; });
    canvas.addEventListener('pointerup', event => {
      if (event.button !== 0 || !this.pointerStart || Math.hypot(event.clientX - this.pointerStart.x,
        event.clientY - this.pointerStart.y) > 5) return;
      this.pick(event);
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
    const axes = new THREE.AxesHelper(Math.max(2, Math.min(...size) * .22));
    axes.position.set(0, 0, .02);
    this.environment.add(axes);
    this.fit();
  }

  geometryFor(cell) {
    if (!cell.geometry) return new THREE.SphereGeometry(Math.min(...this.domain.spacing_um_xyz) * .2, 8, 6);
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
    for (const cell of snapshot.frame.cells) {
      const mesh = new THREE.Mesh(this.geometryFor(cell), !cell.geometry ? this.unknownMaterial :
        cell.id === selectedId ? this.selectedMaterial : this.material);
      mesh.position.fromArray(cell.position_um);
      mesh.quaternion.fromArray(cell.orientation_xyzw);
      mesh.userData.cellId = cell.id;
      mesh.renderOrder = cell.id === selectedId ? 3 : 2;
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
    this.clear(this.blocks);
    this.blockHits = [];
    this.blockData = blocks;
    this.selectedBlock = selectedBlock;
    for (const block of blocks) {
      const geometry = new THREE.BoxGeometry(...block.size);
      const material = new THREE.MeshBasicMaterial({ color: block.id === selectedBlock ? 0xf3ba78 : 0x4dcaaf,
        transparent: true, opacity: .045, depthWrite: false, side: THREE.DoubleSide });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.fromArray(block.center);
      mesh.userData.blockId = block.id;
      const outline = new THREE.LineSegments(new THREE.EdgesGeometry(geometry),
        new THREE.LineBasicMaterial({ color: block.id === selectedBlock ? 0xf3ba78 : 0x59bda9,
          transparent: true, opacity: block.id === selectedBlock ? .9 : .43 }));
      mesh.add(outline);
      this.blocks.add(mesh);
      this.blockHits.push(mesh);
    }
    this.blocks.visible = this.mode === 'space';
    this.request();
  }

  setMode(mode) {
    this.mode = mode;
    this.blocks.visible = mode === 'space';
    this.request();
  }

  setTool(tool) { this.tool = tool; this.canvas.style.cursor = tool === 'population' ? 'crosshair' : 'default'; }

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
      if (this.tool === 'population') {
        const point = this.raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1),
          -this.size[2] / 2), new THREE.Vector3());
        if (point) this.callbacks.placePopulation(point.toArray());
        return;
      }
      const id = this.raycaster.intersectObjects(this.blockHits ?? [])[0]?.object.userData.blockId;
      if (id) this.callbacks.selectBlock(id);
      return;
    }
    if (!this.meshes?.length) return;
    const ids = [...new Set(this.raycaster.intersectObjects(this.meshes).map(hit => hit.object.userData.cellId))];
    if (ids.length) this.callbacks.selectCell(nextHit(ids, this.selectedId));
  }

  setCamera(view) {
    this.view = view;
    const center = new THREE.Vector3(...this.size.map(value => value / 2));
    const radius = new THREE.Vector3(...this.size).length() / 2;
    const aspect = this.canvas.clientWidth / Math.max(1, this.canvas.clientHeight);
    const height = Math.max(radius * 2.5, 2);
    this.orthographic.left = -height * aspect / 2;
    this.orthographic.right = height * aspect / 2;
    this.orthographic.top = height / 2;
    this.orthographic.bottom = -height / 2;
    this.orthographic.updateProjectionMatrix();
    this.camera = view === 'perspective' ? this.perspective : this.orthographic;
    this.orthographic.zoom = 1;
    const direction = ({ top: [0, 0, 1], front: [0, -1, 0], right: [1, 0, 0],
      orthographic: [1, -1.25, .9], perspective: [1, -1.25, .9] })[view] ?? [1, -1.25, .9];
    this.camera.position.copy(center).add(new THREE.Vector3(...direction).normalize().multiplyScalar(radius * 3.4));
    this.camera.up.set(0, view === 'top' ? 1 : 0, view === 'top' ? 0 : 1);
    this.camera.lookAt(center);
    this.orbit.object = this.camera;
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
