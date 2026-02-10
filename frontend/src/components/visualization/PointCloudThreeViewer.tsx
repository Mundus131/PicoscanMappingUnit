/**
 * PointCloudThreeViewer - stable viewer with OrbitControls
 */

'use client';

import React, { useEffect, useRef, useImperativeHandle, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

interface PointCloudThreeViewerProps {
  points: number[][]; // [x, y, z, rssi?]
  width?: number | string;
  height?: number | string;
  mapAxes?: 'xyz' | 'xzy'; // default xzy to render on X/Y
  view?: '3d' | '2d';
  lockX?: boolean;
  lockY?: boolean;
  showGrid?: boolean;
  gridSize?: number;
  gridStep?: number;
  colorScaleMode?: 'auto' | 'rssi100' | 'rssi255';
  colorMode?: 'rssi' | 'x' | 'y' | 'z';
  onHoverPoint?: (point: number[] | null) => void;
  showOriginAxes?: boolean;
  originAxisSize?: number;
  showFrame?: boolean;
  frameSizeMm?: { width: number; height: number } | null;
  frameOriginMode?: 'center' | 'bottom-left' | 'front-left';
  framePlane?: 'xy' | 'xz';
  markers?: {
    position: [number, number, number];
    color?: number;
    size?: number;
    label?: string;
    yawDeg?: number;
    fovDeg?: number;
    rangeMm?: number;
  }[];
  yHorizontal?: boolean;
  annotations?: {
    start: [number, number, number];
    end: [number, number, number];
    color?: number;
    label?: string;
  }[];
  showAxisWidget?: boolean;
  showFloor?: boolean;
}

export interface PointCloudThreeViewerHandle {
  resetView: () => void;
  fitToPoints: () => void;
}

function createCircleTexture(): THREE.Texture {
  const size = 64;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.clearRect(0, 0, size, size);
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2 - 2, 0, Math.PI * 2);
    ctx.closePath();
    ctx.fillStyle = '#ffffff';
    ctx.fill();
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

const PointCloudThreeViewer = React.forwardRef<PointCloudThreeViewerHandle, PointCloudThreeViewerProps>(
  function PointCloudThreeViewer(
    {
      points,
      width = '100%',
      height = '100%',
      mapAxes = 'xzy',
      view = '2d',
      lockX = false,
      lockY = false,
      showGrid = true,
      gridSize = 5000,
      gridStep = 500,
      colorScaleMode = 'auto',
      colorMode = 'rssi',
      onHoverPoint,
      showOriginAxes = false,
      originAxisSize = 1000,
      showFrame = false,
      frameSizeMm = null,
      frameOriginMode = 'center',
      framePlane = 'xy',
      markers = [],
      yHorizontal = false,
      annotations = [],
      showAxisWidget = true,
      showFloor = true,
    },
    ref
  ) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | THREE.OrthographicCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const pointsRef = useRef<THREE.Points | null>(null);
  const raycasterRef = useRef<THREE.Raycaster | null>(null);
  const mouseRef = useRef<THREE.Vector2>(new THREE.Vector2());
  const lastHoverIndexRef = useRef<number | null>(null);
  const animationRef = useRef<number | null>(null);
  const centerRef = useRef<THREE.Vector3>(new THREE.Vector3(0, 0, 0));
  const defaultCameraPosRef = useRef<THREE.Vector3>(new THREE.Vector3(0, 0, 0));
  const defaultCameraUpRef = useRef<THREE.Vector3>(new THREE.Vector3(0, 1, 0));
  const defaultTargetRef = useRef<THREE.Vector3>(new THREE.Vector3(0, 0, 0));
  const axisViewRef = useRef<{ axis: 'x' | 'y' | 'z' | null; sign: 1 | -1 }>({ axis: null, sign: 1 });
  const circleTextureRef = useRef<THREE.Texture | null>(null);
  const gridGroupRef = useRef<THREE.Group | null>(null);
  const originAxesRef = useRef<THREE.Group | null>(null);
  const frameGroupRef = useRef<THREE.Group | null>(null);
  const markersGroupRef = useRef<THREE.Group | null>(null);
  const annotationsGroupRef = useRef<THREE.Group | null>(null);
  const axisWidgetRef = useRef<SVGSVGElement | null>(null);
  const axisLineRefs = useRef<Record<string, SVGLineElement | null>>({ x: null, y: null, z: null });
  const axisLabelRefs = useRef<Record<string, SVGTextElement | null>>({ x: null, y: null, z: null });
  const axisHitRefs = useRef<Record<string, SVGCircleElement | null>>({ x: null, y: null, z: null });
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    const apply = () => {
      setIsDark(document.documentElement.classList.contains('dark'));
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;

    const container = containerRef.current;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(isDark ? 0x0b1220 : 0xf9fafb);
    sceneRef.current = scene;

    let camera: THREE.PerspectiveCamera | THREE.OrthographicCamera;
    if (view === '2d') {
      const size = 1000;
      camera = new THREE.OrthographicCamera(-size, size, size, -size, 0.1, 100000);
      camera.position.set(0, 0, 2000);
      camera.lookAt(0, 0, 0);
    } else {
      camera = new THREE.PerspectiveCamera(60, 1, 1, 100000);
      if (yHorizontal) {
        camera.up.set(1, 0, 0);
        camera.position.set(0, 0, 2000);
      } else {
        camera.position.set(1200, 800, 800);
      }
    }
    cameraRef.current = camera;
    defaultCameraPosRef.current.copy(camera.position);
    defaultCameraUpRef.current.copy(camera.up);
    defaultTargetRef.current.set(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    rendererRef.current = renderer;
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.1;
    controls.rotateSpeed = 0.5;
    controls.zoomSpeed = 0.8;
    controls.panSpeed = 0.9;
    controls.screenSpacePanning = true;
    controls.mouseButtons = {
      LEFT: THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.PAN,
    };
    if (view === '2d') {
      controls.enableRotate = false;
      controls.enablePan = true;
      controls.enableZoom = true;
    }
    controlsRef.current = controls;

    const createTextSprite = (text: string, color = '#1f2937', fontSize = 44, bgColor = 'rgba(255,255,255,0.95)') => {
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      const size = 256;
      canvas.width = size;
      canvas.height = size;
      if (ctx) {
        ctx.clearRect(0, 0, size, size);
        ctx.font = `${fontSize}px Arial`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const padding = 10;
        const metrics = ctx.measureText(text);
        const textWidth = metrics.width + padding * 2;
        const textHeight = fontSize + padding * 2;
        ctx.fillStyle = bgColor;
        ctx.fillRect((size - textWidth) / 2, (size - textHeight) / 2, textWidth, textHeight);
        ctx.fillStyle = color;
        ctx.fillText(text, size / 2, size / 2);
      }
      const texture = new THREE.CanvasTexture(canvas);
      const material = new THREE.SpriteMaterial({
        map: texture,
        transparent: true,
        depthTest: false,
        depthWrite: false,
      });
      const sprite = new THREE.Sprite(material);
      sprite.scale.set(520, 520, 1);
      sprite.renderOrder = 10;
      return sprite;
    };

    if (showGrid && view === '2d') {
      const gridGroup = new THREE.Group();
      gridGroup.renderOrder = 1;
      gridGroupRef.current = gridGroup;

      const minorColor = new THREE.LineBasicMaterial({
        color: isDark ? 0x1f2937 : 0xe5e7eb,
        transparent: true,
        opacity: isDark ? 0.7 : 0.8,
      });
      const majorColor = new THREE.LineBasicMaterial({
        color: isDark ? 0x334155 : 0xcbd5f5,
        transparent: true,
        opacity: isDark ? 0.85 : 0.9,
      });
      const axisColorX = new THREE.LineBasicMaterial({ color: isDark ? 0xf87171 : 0xef4444 });
      const axisColorY = new THREE.LineBasicMaterial({ color: isDark ? 0x34d399 : 0x22c55e });
      const tickColor = new THREE.LineBasicMaterial({
        color: isDark ? 0x475569 : 0x94a3b8,
        transparent: true,
        opacity: 0.95,
      });

      const max = gridSize;
      const step = gridStep;
      const majorStep = gridStep * 2;

      for (let x = -max; x <= max; x += step) {
        const mat = (x % majorStep === 0) ? majorColor : minorColor;
        const geo = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(x, -max, -1),
          new THREE.Vector3(x, max, -1),
        ]);
        gridGroup.add(new THREE.Line(geo, mat));
      }

      for (let y = -max; y <= max; y += step) {
        const mat = (y % majorStep === 0) ? majorColor : minorColor;
        const geo = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(-max, y, -1),
          new THREE.Vector3(max, y, -1),
        ]);
        gridGroup.add(new THREE.Line(geo, mat));
      }

      gridGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(-max, 0, -0.5),
        new THREE.Vector3(max, 0, -0.5),
      ]), axisColorX));
      gridGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, -max, -0.5),
        new THREE.Vector3(0, max, -0.5),
      ]), axisColorY));

      // Tick marks and labels along axes (meters)
      const tickSize = gridStep * 0.18;
      for (let x = -max; x <= max; x += majorStep) {
        if (x === 0) continue;
        const tickGeo = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(x, -tickSize, -0.5),
          new THREE.Vector3(x, tickSize, -0.5),
        ]);
        gridGroup.add(new THREE.Line(tickGeo, tickColor));
        const label = createTextSprite(
          (x / 1000).toFixed(1),
          isDark ? '#e2e8f0' : '#111827',
          44,
          isDark ? 'rgba(15,23,42,0.9)' : 'rgba(255,255,255,0.95)'
        );
        label.position.set(x, -tickSize * 5.2, 0);
        gridGroup.add(label);

        // Additional in-grid label (top edge)
        const topLabel = createTextSprite(
          (x / 1000).toFixed(1),
          isDark ? '#94a3b8' : '#6b7280',
          36,
          isDark ? 'rgba(15,23,42,0.7)' : 'rgba(255,255,255,0.8)'
        );
        topLabel.position.set(x, max - tickSize * 3.2, 0);
        gridGroup.add(topLabel);

        // In-grid label near center line
        const midLabel = createTextSprite(
          (x / 1000).toFixed(1),
          isDark ? '#cbd5f5' : '#374151',
          40,
          isDark ? 'rgba(15,23,42,0.85)' : 'rgba(255,255,255,0.9)'
        );
        midLabel.position.set(x, gridStep * 1.2, 0);
        gridGroup.add(midLabel);
      }

      for (let y = -max; y <= max; y += majorStep) {
        if (y === 0) continue;
        const tickGeo = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(-tickSize, y, -0.5),
          new THREE.Vector3(tickSize, y, -0.5),
        ]);
        gridGroup.add(new THREE.Line(tickGeo, tickColor));
        const label = createTextSprite(
          (y / 1000).toFixed(1),
          isDark ? '#e2e8f0' : '#111827',
          44,
          isDark ? 'rgba(15,23,42,0.9)' : 'rgba(255,255,255,0.95)'
        );
        label.position.set(tickSize * 6.6, y, 0);
        gridGroup.add(label);

        // Additional in-grid label (left edge)
        const leftLabel = createTextSprite(
          (y / 1000).toFixed(1),
          isDark ? '#94a3b8' : '#6b7280',
          36,
          isDark ? 'rgba(15,23,42,0.7)' : 'rgba(255,255,255,0.8)'
        );
        leftLabel.position.set(-max + tickSize * 3.2, y, 0);
        gridGroup.add(leftLabel);

        // In-grid label near center line
        const midLabel = createTextSprite(
          (y / 1000).toFixed(1),
          isDark ? '#cbd5f5' : '#374151',
          40,
          isDark ? 'rgba(15,23,42,0.85)' : 'rgba(255,255,255,0.9)'
        );
        midLabel.position.set(gridStep * 1.2, y, 0);
        gridGroup.add(midLabel);
      }

      const xLabel = createTextSprite('x [m]', isDark ? '#f87171' : '#ef4444', 50, isDark ? 'rgba(15,23,42,0.9)' : 'rgba(255,255,255,0.95)');
      xLabel.position.set(max - gridStep, -tickSize * 7.6, 0);
      gridGroup.add(xLabel);
      const yLabel = createTextSprite('y [m]', isDark ? '#34d399' : '#22c55e', 50, isDark ? 'rgba(15,23,42,0.9)' : 'rgba(255,255,255,0.95)');
      yLabel.position.set(tickSize * 9.5, max - gridStep, 0);
      gridGroup.add(yLabel);

      scene.add(gridGroup);
    }
    let gridXZRef: THREE.GridHelper | null = null;
    let gridYZRef: THREE.GridHelper | null = null;
    if (showGrid && view === '3d') {
      const gridGroup = new THREE.Group();
      gridGroup.renderOrder = 1;
      gridGroupRef.current = gridGroup;

      const size = gridSize;
      const divisions = Math.max(2, Math.round(size / gridStep));
      const makeGrid = (colorMajor: number, colorMinor: number) => {
        const g = new THREE.GridHelper(size, divisions, colorMajor, colorMinor);
        g.material.opacity = isDark ? 0.3 : 0.4;
        g.material.transparent = true;
        return g;
      };
      const gridXZ = makeGrid(isDark ? 0x334155 : 0xcbd5f5, isDark ? 0x1f2937 : 0xe5e7eb);
      const gridXY = makeGrid(isDark ? 0x334155 : 0xcbd5f5, isDark ? 0x1f2937 : 0xe5e7eb);
      const gridYZ = makeGrid(isDark ? 0x334155 : 0xcbd5f5, isDark ? 0x1f2937 : 0xe5e7eb);
      gridXZ.rotation.set(0, 0, 0);
      gridXZ.position.set(0, -1, 0);
      gridXY.rotation.set(Math.PI / 2, 0, 0);
      gridYZ.rotation.set(0, 0, Math.PI / 2);
      gridXZRef = gridXZ;
      gridYZRef = gridYZ;
      gridGroup.add(gridXZ, gridXY, gridYZ);

      const createTextSprite = (text: string, color = '#94a3b8') => {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        const size = 256;
        canvas.width = size;
        canvas.height = size;
        if (ctx) {
          ctx.clearRect(0, 0, size, size);
          ctx.font = '36px Arial';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillStyle = isDark ? 'rgba(15,23,42,0.8)' : 'rgba(255,255,255,0.85)';
          ctx.fillRect(10, 90, size - 20, 60);
          ctx.fillStyle = color;
          ctx.fillText(text, size / 2, size / 2);
        }
        const texture = new THREE.CanvasTexture(canvas);
        const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false });
        const sprite = new THREE.Sprite(material);
        sprite.scale.set(420, 140, 1);
        sprite.renderOrder = 2;
        return sprite;
      };

      const step = gridStep;
      for (let x = -size / 2; x <= size / 2; x += step) {
        if (x === 0) continue;
        const label = createTextSprite((x / 1000).toFixed(1), isDark ? '#94a3b8' : '#6b7280');
        label.position.set(x, 0, -size / 2);
        gridGroup.add(label);
      }
      for (let z = -size / 2; z <= size / 2; z += step) {
        if (z === 0) continue;
        const label = createTextSprite((z / 1000).toFixed(1), isDark ? '#94a3b8' : '#6b7280');
        label.position.set(-size / 2, 0, z);
        gridGroup.add(label);
      }

      scene.add(gridGroup);
    }

    if (showOriginAxes) {
      const axesGroup = new THREE.Group();
      originAxesRef.current = axesGroup;
      const xMat = new THREE.LineBasicMaterial({ color: 0xef4444 });
      const yMat = new THREE.LineBasicMaterial({ color: 0x22c55e });
      const zMat = new THREE.LineBasicMaterial({ color: 0x3b82f6 });
      const size = originAxisSize;
      axesGroup.add(new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(size, 0, 0)]),
        xMat
      ));
      axesGroup.add(new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, size, 0)]),
        yMat
      ));
      axesGroup.add(new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, size)]),
        zMat
      ));
      scene.add(axesGroup);
    }

    if (view === '3d' && showFloor) {
      const floorSize = gridSize * 2.0;
      const floorGeo = new THREE.PlaneGeometry(floorSize, floorSize, 1, 1);
      const canvas = document.createElement('canvas');
      canvas.width = 256;
      canvas.height = 256;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        const tile = 32;
        for (let y = 0; y < 256; y += tile) {
          for (let x = 0; x < 256; x += tile) {
            const even = ((x / tile) + (y / tile)) % 2 === 0;
            if (isDark) {
              ctx.fillStyle = even ? '#0f172a' : '#111827';
            } else {
              ctx.fillStyle = even ? '#e5e7eb' : '#f1f5f9';
            }
            ctx.fillRect(x, y, tile, tile);
          }
        }
      }
      const tex = new THREE.CanvasTexture(canvas);
      tex.wrapS = THREE.RepeatWrapping;
      tex.wrapT = THREE.RepeatWrapping;
      tex.repeat.set(8, 8);
      const floorMat = new THREE.MeshBasicMaterial({
        map: tex,
        color: 0xffffff,
        transparent: true,
        opacity: 1,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const floor = new THREE.Mesh(floorGeo, floorMat);
      floor.rotation.y = Math.PI / 2;
      floor.position.set(0.2, 0, 0);
      floor.renderOrder = 3;
      scene.add(floor);
      // Hide grid on the floor plane
      if (gridXZRef) gridXZRef.visible = false;
      if (gridYZRef) gridYZRef.visible = false;
    }

    if (showFrame && frameSizeMm) {
      const group = new THREE.Group();
      frameGroupRef.current = group;

      const mat = new THREE.LineBasicMaterial({ color: isDark ? 0x94a3b8 : 0x64748b });
      const plane = framePlane === 'xz' ? 'xz' : 'xy';
      const toVec = (a: number, b: number) => (plane === 'xz'
        ? new THREE.Vector3(a, 0, b)
        : new THREE.Vector3(a, b, 0));
      const halfW = frameSizeMm.width / 2;
      const halfH = frameSizeMm.height / 2;
      const minX = frameOriginMode === 'center' ? -halfW : 0;
      const maxX = frameOriginMode === 'center' ? halfW : frameSizeMm.width;
      const minY = frameOriginMode === 'center' ? -halfH : 0;
      const maxY = frameOriginMode === 'center' ? halfH : frameSizeMm.height;

      const pts = [
        toVec(minX, minY),
        toVec(maxX, minY),
        toVec(maxX, maxY),
        toVec(minX, maxY),
        toVec(minX, minY),
      ];
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      group.add(new THREE.Line(geo, mat));

      const createLabelSprite = (text: string) => {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        const size = 256;
        canvas.width = size;
        canvas.height = size;
        if (ctx) {
          ctx.clearRect(0, 0, size, size);
          ctx.font = '36px Arial';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillStyle = isDark ? 'rgba(15,23,42,0.85)' : 'rgba(255,255,255,0.9)';
          ctx.fillRect(10, 90, size - 20, 60);
          ctx.fillStyle = isDark ? '#e2e8f0' : '#111827';
          ctx.fillText(text, size / 2, size / 2);
        }
        const texture = new THREE.CanvasTexture(canvas);
        const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false });
        const sprite = new THREE.Sprite(material);
        sprite.scale.set(400, 120, 1);
        return sprite;
      };

      const widthLabel = createLabelSprite(`${(frameSizeMm.width / 1000).toFixed(2)} m`);
      widthLabel.position.copy(
        toVec((minX + maxX) / 2, maxY + frameSizeMm.height * 0.05)
      );
      group.add(widthLabel);

      const heightLabel = createLabelSprite(`${(frameSizeMm.height / 1000).toFixed(2)} m`);
      heightLabel.position.copy(
        toVec(maxX + frameSizeMm.width * 0.05, (minY + maxY) / 2)
      );
      group.add(heightLabel);

      scene.add(group);
    }

    const resize = () => {
      const rect = container.getBoundingClientRect();
      const w = rect.width || 1;
      const h = rect.height || 1;
      renderer.setSize(w, h, false);
      if (camera instanceof THREE.PerspectiveCamera) {
        camera.aspect = w / h;
      } else {
        const size = 1000;
        camera.left = -size * (w / h);
        camera.right = size * (w / h);
        camera.top = size;
        camera.bottom = -size;
      }
      camera.updateProjectionMatrix();
    };

    resize();
    const onResize = () => resize();
    window.addEventListener('resize', onResize);

    const animate = () => {
      controls.update();
      if (lockX || lockY) {
        const target = controls.target;
        const cam = camera;
        if (lockX) {
          target.x = centerRef.current.x;
          cam.position.x = centerRef.current.x;
        }
        if (lockY) {
          target.y = centerRef.current.y;
          cam.position.y = centerRef.current.y;
        }
      }
      if (showAxisWidget && view === '3d' && axisWidgetRef.current && cameraRef.current) {
        const cam = cameraRef.current;
        const q = cam.quaternion.clone().invert();
        const scale = 34;
        const center = 44;
        const axes = {
          x: new THREE.Vector3(1, 0, 0).applyQuaternion(q),
          y: new THREE.Vector3(0, 1, 0).applyQuaternion(q),
          z: new THREE.Vector3(0, 0, 1).applyQuaternion(q),
        };
          (['x', 'y', 'z'] as const).forEach((k) => {
            const v = axes[k];
            const x = center + v.x * scale;
            const y = center - v.y * scale;
            const line = axisLineRefs.current[k];
            if (line) {
              line.setAttribute('x1', String(center));
              line.setAttribute('y1', String(center));
              line.setAttribute('x2', String(x));
              line.setAttribute('y2', String(y));
            }
            const label = axisLabelRefs.current[k];
            if (label) {
              label.setAttribute('x', String(center + v.x * (scale + 8)));
              label.setAttribute('y', String(center - v.y * (scale + 8)));
            }
            const hit = axisHitRefs.current[k];
            if (hit) {
              hit.setAttribute('cx', String(x));
              hit.setAttribute('cy', String(y));
            }
          });
        }
      renderer.render(scene, camera);
      animationRef.current = requestAnimationFrame(animate);
    };
    animate();

    raycasterRef.current = new THREE.Raycaster();
    raycasterRef.current.params.Points = { threshold: 8 };

    const handlePick = (event: MouseEvent, clearOnMiss: boolean) => {
      if (!onHoverPoint || !pointsRef.current) return;
      const rect = renderer.domElement.getBoundingClientRect();
      const x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      const y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      mouseRef.current.set(x, y);
      const raycaster = raycasterRef.current;
      if (!raycaster || !cameraRef.current) return;
      raycaster.setFromCamera(mouseRef.current, cameraRef.current);
      try {
        const cam = cameraRef.current;
        const target = controlsRef.current?.target;
        if (cam && target) {
          const dist = cam.position.distanceTo(target);
          raycaster.params.Points = { threshold: Math.max(6, Math.min(50, dist * 0.01)) };
        }
      } catch {
        // ignore
      }
      const intersects = raycaster.intersectObject(pointsRef.current, false);
      if (intersects.length > 0) {
        const idx = intersects[0].index ?? null;
        if (idx !== null && idx !== lastHoverIndexRef.current) {
          lastHoverIndexRef.current = idx;
          const p = points[idx];
          onHoverPoint(p);
        }
      } else if (clearOnMiss && lastHoverIndexRef.current !== null) {
        lastHoverIndexRef.current = null;
        onHoverPoint(null);
      }
    };

    const handleMouseMove = (event: MouseEvent) => {
      handlePick(event, true);
    };

    const handleClick = (event: MouseEvent) => {
      handlePick(event, false);
    };

    const handleMouseLeave = () => {
      if (!onHoverPoint) return;
      lastHoverIndexRef.current = null;
      onHoverPoint(null);
    };

    renderer.domElement.addEventListener('mousemove', handleMouseMove);
    renderer.domElement.addEventListener('click', handleClick);
    renderer.domElement.addEventListener('mouseleave', handleMouseLeave);

    return () => {
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
      window.removeEventListener('resize', onResize);
      renderer.domElement.removeEventListener('mousemove', handleMouseMove);
      renderer.domElement.removeEventListener('click', handleClick);
      renderer.domElement.removeEventListener('mouseleave', handleMouseLeave);
      controls.dispose();
      renderer.dispose();
      if (renderer.domElement.parentElement) {
        renderer.domElement.parentElement.removeChild(renderer.domElement);
      }
      scene.clear();
    };
  }, [view, showGrid, gridSize, gridStep, isDark, showOriginAxes, originAxisSize, showFrame, frameSizeMm, frameOriginMode, framePlane, yHorizontal, showFloor]);

  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    if (markersGroupRef.current) {
      scene.remove(markersGroupRef.current);
      markersGroupRef.current.clear();
      markersGroupRef.current = null;
    }
    if (!markers || markers.length === 0) return;
    const group = new THREE.Group();
    markersGroupRef.current = group;
    const createLabelSprite = (text: string) => {
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      const size = 256;
      canvas.width = size;
      canvas.height = size;
      if (ctx) {
        ctx.clearRect(0, 0, size, size);
        ctx.font = '36px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = isDark ? 'rgba(15,23,42,0.85)' : 'rgba(255,255,255,0.9)';
        ctx.fillRect(10, 90, size - 20, 60);
        ctx.fillStyle = isDark ? '#e2e8f0' : '#111827';
        ctx.fillText(text, size / 2, size / 2);
      }
      const texture = new THREE.CanvasTexture(canvas);
      const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false });
      const sprite = new THREE.Sprite(material);
      sprite.scale.set(420, 140, 1);
      sprite.renderOrder = 10;
      return sprite;
    };
    markers.forEach((m) => {
      const color = m.color ?? (isDark ? 0x60a5fa : 0x2563eb);
      const size = m.size ?? 40;
      const geom = new THREE.SphereGeometry(size, 12, 12);
      const mat = new THREE.MeshBasicMaterial({ color });
      const mesh = new THREE.Mesh(geom, mat);
      mesh.position.set(m.position[0], m.position[1], m.position[2]);
      group.add(mesh);

      if (m.label) {
        const label = createLabelSprite(m.label);
        if (framePlane === 'xz') {
          label.position.set(m.position[0], m.position[1] + 120, m.position[2]);
        } else {
          label.position.set(m.position[0], m.position[1], m.position[2] + 120);
        }
        group.add(label);
      }

      if (m.fovDeg && m.fovDeg > 0 && m.rangeMm && m.rangeMm > 0) {
        const half = (m.fovDeg / 2) * (Math.PI / 180);
        const shape = new THREE.Shape();
        shape.moveTo(0, 0);
        shape.lineTo(m.rangeMm * Math.cos(-half), m.rangeMm * Math.sin(-half));
        shape.absarc(0, 0, m.rangeMm, -half, half, false);
        shape.lineTo(0, 0);
        const geo = new THREE.ShapeGeometry(shape, 32);
        const yawRad = ((m.yawDeg ?? 0) * Math.PI) / 180;
        if (framePlane === 'xz') {
          geo.rotateX(Math.PI / 2);
          geo.rotateY(yawRad);
        } else {
          geo.rotateZ(yawRad);
        }
        const matSector = new THREE.MeshBasicMaterial({
          color: 0xea580c,
          transparent: true,
          opacity: 0.35,
          side: THREE.DoubleSide,
          depthWrite: false,
        });
        const sector = new THREE.Mesh(geo, matSector);
        sector.rotation.set(0, 0, 0);
        const epsilon = 1;
        if (framePlane === 'xz') {
          sector.position.set(m.position[0], m.position[1] + epsilon, m.position[2]);
        } else {
          sector.position.set(m.position[0], m.position[1], m.position[2] + epsilon);
        }
        group.add(sector);
      }
    });
    scene.add(group);
  }, [markers, isDark, framePlane]);

  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    if (annotationsGroupRef.current) {
      scene.remove(annotationsGroupRef.current);
      annotationsGroupRef.current.clear();
      annotationsGroupRef.current = null;
    }
    if (!annotations || annotations.length === 0) return;
    const group = new THREE.Group();
    annotationsGroupRef.current = group;

    const createLabelSprite = (text: string) => {
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      const size = 256;
      canvas.width = size;
      canvas.height = size;
      if (ctx) {
        ctx.clearRect(0, 0, size, size);
        ctx.font = '36px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = isDark ? 'rgba(15,23,42,0.85)' : 'rgba(255,255,255,0.9)';
        ctx.fillRect(10, 90, size - 20, 60);
        ctx.fillStyle = isDark ? '#e2e8f0' : '#111827';
        ctx.fillText(text, size / 2, size / 2);
      }
      const texture = new THREE.CanvasTexture(canvas);
      const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false });
      const sprite = new THREE.Sprite(material);
      sprite.scale.set(420, 140, 1);
      sprite.renderOrder = 11;
      return sprite;
    };

    annotations.forEach((a) => {
      const color = a.color ?? (isDark ? 0xf59e0b : 0xf97316);
      const mat = new THREE.LineBasicMaterial({ color });
      const start = new THREE.Vector3(a.start[0], a.start[1], a.start[2]);
      const end = new THREE.Vector3(a.end[0], a.end[1], a.end[2]);
      const geo = new THREE.BufferGeometry().setFromPoints([start, end]);
      group.add(new THREE.Line(geo, mat));

      const dir = new THREE.Vector3().subVectors(end, start);
      const len = dir.length();
      if (len > 1e-6) {
        dir.normalize();
        const arrowSize = Math.min(120, Math.max(40, len * 0.15));
        const cone = new THREE.Mesh(
          new THREE.ConeGeometry(arrowSize * 0.35, arrowSize, 16),
          new THREE.MeshBasicMaterial({ color })
        );
        cone.position.copy(end);
        const axis = new THREE.Vector3(0, 1, 0);
        const quat = new THREE.Quaternion().setFromUnitVectors(axis, dir);
        cone.quaternion.copy(quat);
        group.add(cone);
      }

      if (a.label) {
        const label = createLabelSprite(a.label);
        const mid = new THREE.Vector3().addVectors(start, end).multiplyScalar(0.5);
        label.position.set(mid.x, mid.y, mid.z);
        group.add(label);
      }
    });

    scene.add(group);
  }, [annotations, isDark]);

  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;

    // Remove old points
    if (pointsRef.current) {
      scene.remove(pointsRef.current);
      pointsRef.current.geometry.dispose();
      if (Array.isArray(pointsRef.current.material)) {
        pointsRef.current.material.forEach((m) => m.dispose());
      } else {
        pointsRef.current.material.dispose();
      }
      pointsRef.current = null;
    }

    if (!points || points.length === 0) return;

    const count = points.length;
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);

    // Value scaling for colors
    let rssiMin = Infinity;
    let rssiMax = -Infinity;
    let axisMin = Infinity;
    let axisMax = -Infinity;
    for (let i = 0; i < count; i += 1) {
      const p = points[i];
      const rssi = p.length >= 4 ? p[3] : p[2];
      if (rssi < rssiMin) rssiMin = rssi;
      if (rssi > rssiMax) rssiMax = rssi;
      if (colorMode !== 'rssi') {
        const v = colorMode === 'x' ? p[0] : colorMode === 'y' ? p[1] : p[2];
        if (v < axisMin) axisMin = v;
        if (v > axisMax) axisMax = v;
      }
    }
    const denom = rssiMax - rssiMin || 1;
    const axisDenom = axisMax - axisMin || 1;

    for (let i = 0; i < count; i += 1) {
      const p = points[i];
      const x = p[0];
      const y = p[1];
      const z = p[2];
      let px = x;
      let py = y;
      let pz = z;
      if (mapAxes === 'xzy') {
        // Render on X/Y plane: use Z as Y, and Y as Z
        px = x;
        py = z;
        pz = y;
      }

      positions[i * 3 + 0] = px;
      positions[i * 3 + 1] = py;
      positions[i * 3 + 2] = pz;

      const rssi = p.length >= 4 ? p[3] : z;
      let t = 0.0;
      if (colorMode === 'rssi') {
        if (colorScaleMode === 'rssi100' && p.length >= 4) {
          const clamped = Math.max(0, Math.min(100, rssi));
          t = clamped / 100.0;
        } else if (colorScaleMode === 'rssi255' && p.length >= 4) {
          const clamped = Math.max(0, Math.min(255, rssi));
          t = clamped / 255.0;
        } else {
          t = (rssi - rssiMin) / denom;
        }
      } else {
        const v = colorMode === 'x' ? p[0] : colorMode === 'y' ? p[1] : p[2];
        t = (v - axisMin) / axisDenom;
      }
      const color = new THREE.Color();
      color.setHSL((1.0 - t) * 0.7, 1.0, 0.5);
      colors[i * 3 + 0] = color.r;
      colors[i * 3 + 1] = color.g;
      colors[i * 3 + 2] = color.b;
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    if (!circleTextureRef.current) {
      circleTextureRef.current = createCircleTexture();
    }

    const material = new THREE.PointsMaterial({
      size: view === '3d' ? 6 : 14,
      vertexColors: true,
      sizeAttenuation: view !== '3d',
      map: circleTextureRef.current ?? undefined,
      transparent: true,
      alphaTest: 0.5,
    });

    const pts = new THREE.Points(geometry, material);
    pointsRef.current = pts;
    scene.add(pts);
  }, [points, mapAxes, isDark, colorMode, colorScaleMode]);

  const fitToPoints = () => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls || !points || points.length === 0) return;

    let minX = Infinity;
    let minY = Infinity;
    let minZ = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    let maxZ = -Infinity;

    for (let i = 0; i < points.length; i += 1) {
      const p = points[i];
      let px = p[0];
      let py = p[1];
      let pz = p[2];
      if (mapAxes === 'xzy') {
        px = p[0];
        py = p[2];
        pz = p[1];
      }
      if (px < minX) minX = px;
      if (py < minY) minY = py;
      if (pz < minZ) minZ = pz;
      if (px > maxX) maxX = px;
      if (py > maxY) maxY = py;
      if (pz > maxZ) maxZ = pz;
    }

    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const cz = (minZ + maxZ) / 2;
    centerRef.current.set(cx, cy, cz);

    if (camera instanceof THREE.OrthographicCamera) {
      const w = maxX - minX;
      const h = maxY - minY;
      const pad = 1.2;
      let halfW = (w * pad) / 2;
      let halfH = (h * pad) / 2;
      const rect = rendererRef.current?.domElement?.getBoundingClientRect();
      const viewAspect = rect && rect.height > 0 ? rect.width / rect.height : 1;
      const dataAspect = halfW / halfH;
      if (dataAspect < viewAspect) {
        halfW = halfH * viewAspect;
      } else {
        halfH = halfW / viewAspect;
      }
      camera.left = -halfW;
      camera.right = halfW;
      camera.top = halfH;
      camera.bottom = -halfH;
      camera.position.set(cx, cy, 2000);
      camera.lookAt(cx, cy, cz);
      camera.updateProjectionMatrix();
    } else {
      if (yHorizontal) {
        camera.up.set(1, 0, 0);
        camera.position.set(cx, cy, cz + 2000);
      } else {
        camera.position.set(cx + 1200, cy + 800, cz + 800);
      }
      camera.lookAt(cx, cy, cz);
    }
    controls.target.set(cx, cy, cz);
    controls.update();
  };

  const resetView = () => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) return;
    centerRef.current.set(0, 0, 0);
    if (camera instanceof THREE.OrthographicCamera) {
      const size = 1000;
      camera.left = -size;
      camera.right = size;
      camera.top = size;
      camera.bottom = -size;
      camera.position.copy(defaultCameraPosRef.current);
      camera.up.copy(defaultCameraUpRef.current);
      camera.lookAt(defaultTargetRef.current);
      camera.updateProjectionMatrix();
    } else {
      camera.position.copy(defaultCameraPosRef.current);
      camera.up.copy(defaultCameraUpRef.current);
      camera.lookAt(defaultTargetRef.current);
    }
    controls.target.copy(defaultTargetRef.current);
    controls.update();
  };

  useImperativeHandle(ref, () => ({ resetView, fitToPoints }), [points, mapAxes]);

  const setViewAxis = (axis: 'x' | 'y' | 'z') => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) return;
    const target = controls.target.clone();
    const dist = camera.position.distanceTo(target) || 2000;
    let dir = new THREE.Vector3(0, 0, 1);
    if (axis === 'x') dir = new THREE.Vector3(1, 0, 0);
    if (axis === 'y') dir = new THREE.Vector3(0, 1, 0);
    if (axis === 'z') dir = new THREE.Vector3(0, 0, 1);
    const prev = axisViewRef.current;
    const nextSign = (prev.axis === axis) ? (prev.sign === 1 ? -1 : 1) : 1;
    axisViewRef.current = { axis, sign: nextSign };
    camera.position.copy(target.clone().add(dir.multiplyScalar(dist * nextSign)));
    if (yHorizontal) {
      camera.up.set(1, 0, 0);
    } else {
      camera.up.set(0, 1, 0);
    }
    camera.lookAt(target);
    controls.target.copy(target);
    controls.update();
  };

  return (
    <div style={{ width, height, position: 'relative' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      {showAxisWidget && view === '3d' && (
        <div
          style={{
            position: 'absolute',
            left: 12,
            bottom: 12,
            width: 160,
            height: 180,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            padding: 4,
            background: 'transparent',
          }}
        >
          <div style={{ position: 'relative', width: 120, height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg ref={axisWidgetRef} width={120} height={120} style={{ display: 'block' }}>
              <defs>
                <marker id="arrow-x" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto" markerUnits="strokeWidth">
                  <path d="M0,0 L8,4 L0,8 Z" fill="#ef4444" />
                </marker>
                <marker id="arrow-y" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto" markerUnits="strokeWidth">
                  <path d="M0,0 L8,4 L0,8 Z" fill="#22c55e" />
                </marker>
                <marker id="arrow-z" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto" markerUnits="strokeWidth">
                  <path d="M0,0 L8,4 L0,8 Z" fill="#3b82f6" />
                </marker>
              </defs>
              <g onClick={() => setViewAxis('x')} style={{ cursor: 'pointer' }}>
                <line ref={(el) => { axisLineRefs.current.x = el; }} stroke="#ef4444" strokeWidth="2.5" markerEnd="url(#arrow-x)" />
              </g>
              <g onClick={() => setViewAxis('y')} style={{ cursor: 'pointer' }}>
                <line ref={(el) => { axisLineRefs.current.y = el; }} stroke="#22c55e" strokeWidth="2.5" markerEnd="url(#arrow-y)" />
              </g>
              <g onClick={() => setViewAxis('z')} style={{ cursor: 'pointer' }}>
                <line ref={(el) => { axisLineRefs.current.z = el; }} stroke="#3b82f6" strokeWidth="2.5" markerEnd="url(#arrow-z)" />
              </g>
              <g onClick={() => setViewAxis('x')} style={{ cursor: 'pointer' }}>
                <circle ref={(el) => { axisHitRefs.current.x = el; }} cx={0} cy={0} r={14} fill="transparent" />
              </g>
              <g onClick={() => setViewAxis('y')} style={{ cursor: 'pointer' }}>
                <circle ref={(el) => { axisHitRefs.current.y = el; }} cx={0} cy={0} r={14} fill="transparent" />
              </g>
              <g onClick={() => setViewAxis('z')} style={{ cursor: 'pointer' }}>
                <circle ref={(el) => { axisHitRefs.current.z = el; }} cx={0} cy={0} r={14} fill="transparent" />
              </g>
              <text ref={(el) => { axisLabelRefs.current.x = el; }} fontSize="11" fill="#ef4444" onClick={() => setViewAxis('x')} style={{ cursor: 'pointer' }}>X</text>
              <text ref={(el) => { axisLabelRefs.current.y = el; }} fontSize="11" fill="#22c55e" onClick={() => setViewAxis('y')} style={{ cursor: 'pointer' }}>Y</text>
              <text ref={(el) => { axisLabelRefs.current.z = el; }} fontSize="11" fill="#3b82f6" onClick={() => setViewAxis('z')} style={{ cursor: 'pointer' }}>Z</text>
            </svg>
          </div>
          <button className="btn-secondary px-2 py-1" onClick={resetView} title="Reset view">⌂</button>
        </div>
      )}
    </div>
  );
});

export default PointCloudThreeViewer;
