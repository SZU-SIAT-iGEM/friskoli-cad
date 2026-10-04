import * as THREE from 'three';
import { OrbitControls } from './vendor/three/examples/jsm/controls/OrbitControls.js';
import { TransformControls } from './vendor/three/examples/jsm/controls/TransformControls.js';
import { nextHit } from './catalog.mjs';
import { environmentTransformGeometry, isEnvironmentObject } from './placeables.mjs';
import { concentrationSlice } from './field-slice.mjs';

const fmt = value => Number(value.toFixed(2)).toString();

export const canTransformBlock = (block, mode) => mode === 'space' && Boolean(block && !block.hidden && !block.locked);
export const canTransformEnvironment = (object, mode, tool) => mode === 'space' &&
  ['move','scale'].includes(tool) && Boolean(object && !object.hidden && !object.locked && isEnvironmentObject(object.declaration));

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
    // Gizmo previews geometry live; the document changes once when the drag ends.
    this.transform = new TransformControls(this.camera, canvas);
    this.transform.addEventListener('change', () => this.request());
    this.transform.addEventListener('objectChange', () => this.constrainEnvironmentTransform());
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

  setSnapshot(snapshot, selectedId, fieldId = '', slice = 0, range = null, normal = 'z') {
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
    // Screen markers keep small cells visible without changing their geometry.
    if (snapshot.frame.cells.length) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(snapshot.frame.cells.flatMap(cell => cell.position_um), 3));
      geometry.setAttribute('diameter', new THREE.Float32BufferAttribute(snapshot.frame.cells.map(cell => cell.geometry?.diameter_um ?? Math.min(...this.domain.spacing_um_xyz) * .4), 1));
      const material = new THREE.ShaderMaterial({
        uniforms: { viewportHeight: {value:this.canvas.clientHeight}, pixelRatio: {value:this.renderer.getPixelRatio()} },
        vertexShader: `attribute float diameter;
          uniform float viewportHeight; uniform float pixelRatio; varying float visible;
          void main() {
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
            float pixels = diameter * abs(projectionMatrix[1][1]) * viewportHeight / (2.0 * gl_Position.w);
            visible = pixels < 4.0 ? 1.0 : 0.0;
            gl_PointSize = 4.0 * pixelRatio;
          }`,
        fragmentShader: `varying float visible;
          void main() {
            float r = length(gl_PointCoord - vec2(0.5));
            if (visible < 0.5 || r > 0.5) discard;
            gl_FragColor = vec4(0.39, 0.88, 0.77, 1.0 - smoothstep(0.35, 0.5, r));
          }`,
        transparent:true, depthWrite:false
      });
      this.cellMarkers = new THREE.Points(geometry, material);
      this.cellMarkers.renderOrder = 4;
      this.cells.add(this.cellMarkers);
    } else this.cellMarkers = null;
    const field = snapshot.concentrations[fieldId];
    if (field && range) {
      const section = concentrationSlice(field,this.domain,normal,slice);
      const values = section.values, nx = section.width, ny = section.height;
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
      const plane = new THREE.Mesh(new THREE.PlaneGeometry(...section.size),
        new THREE.MeshBasicMaterial({ map: texture, transparent: true, opacity: .47,
          side: THREE.DoubleSide, depthWrite: false }));
      plane.position.fromArray(this.size.map(value=>value/2));
      plane.position.setComponent(section.axes[2],section.coordinate);
      if(normal==='y')plane.rotation.x=Math.PI/2;
      if(normal==='x')plane.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(
        new THREE.Vector3(0,1,0),new THREE.Vector3(0,0,1),new THREE.Vector3(1,0,0)));
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
      const selected = block.id === selectedBlock;
      const geometry = new THREE.BoxGeometry(...block.size);
      const material = new THREE.MeshBasicMaterial({ color: selected ? 0xf3ba78 : 0x4dcaaf,
        transparent: true, opacity: .045, depthWrite: false, side: THREE.DoubleSide });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.fromArray(block.center);
      mesh.rotation.set(...(block.rotation ?? [0,0,0]).map(n => n * Math.PI / 180));
      mesh.userData.blockId = block.id;
      const outline = new THREE.LineSegments(new THREE.EdgesGeometry(geometry),
        new THREE.LineBasicMaterial({ color: selected ? 0xf3ba78 : 0x59bda9,
          transparent: true, opacity: selected ? .9 : .43 }));
      outline.raycast = () => {};
      mesh.add(outline);
      this.blocks.add(mesh);
      // Distributed populations remain selectable from the object list only.
      // Their selected mesh can host the gizmo without intercepting scene picks.
      if (block.dirty) this.blockHits.push(mesh);
    }
    this.blocks.visible = this.mode === 'space';
    this.attachGizmo();
    this.request();
  }

  // Shapes are derived from the same graph parameters submitted to the solver.
  // Source spheres mark release support, never a fabricated concentration field.
  setObjects(objects, selectedId) {
    if (this.transform.object?.userData.nodeId) this.transform.detach();
    this.objectData = objects;
    this.selectedEnvironment = selectedId;
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
    this.attachGizmo();
    this.request();
  }

  // Each object adapter exposes only transformations representable by its protocol.
  attachGizmo() {
    const object = this.objectData?.find(item => item.id === this.selectedEnvironment);
    const objectMesh = this.objectHits?.find(item => item.userData.nodeId === this.selectedEnvironment);
    if (object && objectMesh && canTransformEnvironment(object, this.mode, this.tool)) {
      this.transform.setMode(this.tool === 'move' ? 'translate' : 'scale');
      this.transform.showX = true; this.transform.showY = true;
      this.transform.showZ = this.domain.geometry !== 'thin_layer';
      this.transform.attach(objectMesh);
      return;
    }
    const mesh = this.blocks.children.find(item => item.userData.blockId === this.selectedBlock);
    const block = this.blockData?.find(item => item.id === this.selectedBlock);
    if (mesh && canTransformBlock(block, this.mode) && ['move','rotate','scale'].includes(this.tool)) {
      this.transform.setMode(this.tool === 'move' ? 'translate' : this.tool);
      this.transform.showX = this.tool !== 'rotate' || this.domain.geometry !== 'thin_layer';
      this.transform.showY = this.tool !== 'rotate' || this.domain.geometry !== 'thin_layer';
      this.transform.showZ = !(this.domain.geometry === 'thin_layer' && this.tool !== 'rotate');
      this.transform.attach(mesh);
    } else this.transform.detach();
  }

  commitTransform() {
    const mesh = this.transform.object;
    const object = this.objectData?.find(item => item.id === mesh?.userData.nodeId);
    if (object) {
      if (!canTransformEnvironment(object, this.mode, this.tool)) return;
      const geometry = this.constrainEnvironmentTransform();
      if (geometry) this.callbacks.transformEnvironment?.(object.id, geometry);
      return;
    }
    const block = this.blockData?.find(item => item.id === mesh?.userData.blockId);
    if (!canTransformBlock(block, this.mode)) return;
    const size = block.size.map((value, axis) => value * Math.abs(mesh.scale.getComponent(axis)));
    this.callbacks.transformBlock(block.id, mesh.position.toArray(), size, [mesh.rotation.x,mesh.rotation.y,mesh.rotation.z].map(v => v * 180 / Math.PI));
  }

  constrainEnvironmentTransform() {
    const mesh=this.transform.object, object=this.objectData?.find(item=>item.id===mesh?.userData.nodeId);
    if (!object || !canTransformEnvironment(object,this.mode,this.tool)) return null;
    const size=object.kind==='local_source' ? null : object.upper.map((v,i)=>v-object.lower[i]);
    // Keep scaling anchored to the stored center: repeated odd/even voxel widths
    // must not accumulate half-voxel translations while the pointer moves.
    const center=this.tool==='scale' ? (size ? object.lower.map((v,i)=>(v+object.upper[i])/2) : object.center) : mesh.position.toArray();
    const axis=Math.max(0,'XYZ'.indexOf(this.transform.axis?.[0] ?? 'X'));
    const radius=object.radius*Math.max(Number.EPSILON,Math.abs(mesh.scale.getComponent(axis)));
    let geometry;
    try {geometry=environmentTransformGeometry(object,this.domain,{center,size:size?.map((v,i)=>Math.max(Number.EPSILON,v*mesh.scale.getComponent(i))),radius});}
    catch {return null;}
    mesh.position.fromArray(geometry.center);
    if (object.kind==='local_source') mesh.scale.setScalar(geometry.radius/object.radius);
    else mesh.scale.fromArray(geometry.size.map((v,i)=>v/size[i]));
    return geometry;
  }

  setTrajectory(segments){
    if(this.trajectory){for(const child of this.trajectory.children){child.geometry.dispose();child.material.dispose();}this.world.remove(this.trajectory);}
    this.trajectory=new THREE.Group();for(const points of segments){const geometry=new THREE.BufferGeometry().setFromPoints(points.map(p=>new THREE.Vector3(...p)));this.trajectory.add(new THREE.Line(geometry,new THREE.LineBasicMaterial({color:0xffc481,transparent:true,opacity:.85})));}this.world.add(this.trajectory);this.request();
  }
  captureView(){return {view:this.view,position:this.camera.position.toArray(),target:this.orbit.target.toArray(),zoom:this.camera.zoom};}
  restoreView(value){if(!value||!Array.isArray(value.position)||!Array.isArray(value.target))return;if(["perspective","orthographic","top","front","right"].includes(value.view))this.setCamera(value.view);this.camera.position.fromArray(value.position);this.orbit.target.fromArray(value.target);this.camera.zoom=Number.isFinite(value.zoom)&&value.zoom>0?value.zoom:1;this.camera.updateProjectionMatrix();this.orbit.update();this.request();}
  setPlacementPreview(block){
    if(this.placementPreview){this.world.remove(this.placementPreview);this.placementPreview.geometry.dispose();this.placementPreview.material.dispose();this.placementPreview=null;}
    if(block){const geometry=new THREE.EdgesGeometry(new THREE.BoxGeometry(...block.size));const mesh=new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({color:0xffc481,transparent:true,opacity:.9}));mesh.position.fromArray(block.center);mesh.rotation.set(...(block.rotation??[0,0,0]).map(v=>v*Math.PI/180));this.world.add(mesh);this.placementPreview=mesh;}
    this.request();
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
    this.measureEndpoints = [];
    this.clear(this.measureGroup);
    this.callbacks.measure?.(null);
  }

  // Only rendered object/cell surfaces are eligible, never population proxy volumes.
  measurementEndpoint() {
    const visible = mesh => {
      for (let parent=mesh; parent; parent=parent.parent) if (!parent.visible) return false;
      return (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).some(material => material?.visible);
    };
    const candidates = [...(this.objectHits ?? []), ...(this.meshes ?? [])].filter(visible);
    for (const mesh of candidates) mesh.updateWorldMatrix(true, false);
    const knownCells = new Map((this.snapshot?.frame?.cells ?? []).map(cell => [cell.id, cell]));
    for (const hit of this.raycaster.intersectObjects(candidates, false)) {
      const id = hit.object.userData.nodeId;
      if (id) return {point:hit.point.clone(), source:'surface', id};
      const cellId = hit.object.userData.cellIds?.[hit.instanceId];
      if (cellId && knownCells.get(cellId)?.geometry) return {point:hit.point.clone(), source:'cell', id:cellId};
    }
    const normal = this.camera.getWorldDirection(new THREE.Vector3());
    const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(normal, new THREE.Vector3(...this.size.map(v => v/2)));
    const point = this.raycaster.ray.intersectPlane(plane, new THREE.Vector3());
    return point ? {point, source:'reference-plane'} : null;
  }

  // World-space surface hits take priority; a third click starts a new measurement.
  addMeasurePoint() {
    if (!this.size || this.mode !== 'space') return;
    const endpoint = this.measurementEndpoint();
    if (!endpoint) return;
    if (this.measurePoints.length >= 2) this.clearMeasure();
    this.measurePoints.push(endpoint.point);
    (this.measureEndpoints ??= []).push({position:endpoint.point.toArray(),source:endpoint.source,...(endpoint.id ? {id:endpoint.id} : {})});
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
      this.callbacks.measure?.({ distance: a.distanceTo(b), delta: b.clone().sub(a).toArray(), endpoints:structuredClone(this.measureEndpoints) });
    } else {
      this.callbacks.measure?.({distance:null, delta:null, endpoints:structuredClone(this.measureEndpoints)});
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
    if (this.mode !== 'space') return null;
    this.pointFromEvent(event);
    return this.raycaster.intersectObjects(this.blockHits ?? [], false)[0]?.object.userData.blockId ?? null;
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
      const id = this.raycaster.intersectObjects(this.blockHits ?? [], false)[0]?.object.userData.blockId;
      if (id) this.callbacks.selectBlock(id);
      return;
    }
    const objectId = this.raycaster.intersectObjects(this.objectHits ?? [],false)[0]?.object.userData.nodeId;
    if (objectId) { this.callbacks.selectEnvironment?.(objectId); return; }
    if (!this.meshes?.length) return;
    const ids = [...new Set(this.raycaster.intersectObjects(this.meshes).map(hit => hit.object.userData.cellIds[hit.instanceId]))];
    if (ids.length) { this.callbacks.selectCell(nextHit(ids, this.selectedId)); return; }
    // Picking uses the same screen coordinates as the position markers.
    if (!this.snapshot?.frame.cells.length) return;
    const rect = this.canvas.getBoundingClientRect(), threshold = event.pointerType === 'touch' ? 14 : 7;
    const near = [];
    for (const cell of this.snapshot?.frame.cells ?? []) {
      const projected = new THREE.Vector3(...cell.position_um).project(this.camera);
      if (projected.z < -1 || projected.z > 1) continue;
      const distance = Math.hypot(rect.left + (projected.x + 1) * rect.width / 2 - event.clientX,
        rect.top + (1 - projected.y) * rect.height / 2 - event.clientY);
      if (distance <= threshold) near.push({id:cell.id, distance, depth:projected.z});
    }
    near.sort((a,b) => a.distance - b.distance || a.depth - b.depth);
    if (near.length) this.callbacks.selectCell(nextHit(near.map(cell => cell.id), this.selectedId));
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
  focusBounds(center,extent) {
    if(!this.size||![...center,...extent].every(Number.isFinite))return;
    const target=new THREE.Vector3(...center),direction=this.camera.position.clone().sub(this.orbit.target).normalize();
    const aspect=this.canvas.clientWidth/Math.max(1,this.canvas.clientHeight),radius=Math.max(1,...extent)/2;
    this.orbit.target.copy(target);
    if(this.camera.isOrthographicCamera){
      const available=(this.camera.top-this.camera.bottom)*Math.min(1,aspect);
      this.camera.zoom=Math.max(.1,Math.min(20,available/(radius*2.6)));
      this.camera.updateProjectionMatrix();
      this.camera.position.copy(target).add(direction.multiplyScalar(Math.max(radius*4,this.size[2]*2)));
    }else{
      const angle=Math.atan(Math.tan(THREE.MathUtils.degToRad(this.camera.fov/2))*Math.min(1,aspect));
      this.camera.position.copy(target).add(direction.multiplyScalar(radius/Math.sin(angle)*1.3));
    }
    this.orbit.update();this.request();
  }

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
      if (this.cellMarkers) this.cellMarkers.material.uniforms.viewportHeight.value = this.canvas.clientHeight;
      this.renderer.render(this.world, this.camera);
      this.drawAnnotations();
    });
  }

  drawAnnotations() {
    this.annotations.replaceChildren();
    if (!this.snapshot) return;
    if (this.snapshot.frame.cells.length) {
      const note = document.createElement('span');
      note.className = 'scene-marker-note';
      note.textContent = this.callbacks.markerLabel?.() ?? 'Cell position markers · min 4 px; geometry to scale';
      this.annotations.append(note);
    }
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
