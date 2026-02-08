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
    },
    ref
  ) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | THREE.OrthographicCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const pointsRef = useRef<THREE.Points | null>(null);
  const animationRef = useRef<number | null>(null);
  const centerRef = useRef<THREE.Vector3>(new THREE.Vector3(0, 0, 0));
  const circleTextureRef = useRef<THREE.Texture | null>(null);
  const gridGroupRef = useRef<THREE.Group | null>(null);
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
      camera.position.set(1200, 800, 800);
    }
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    rendererRef.current = renderer;
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.1;
    controls.rotateSpeed = 0.5;
    controls.zoomSpeed = 0.8;
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
      renderer.render(scene, camera);
      animationRef.current = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
      window.removeEventListener('resize', onResize);
      controls.dispose();
      renderer.dispose();
      if (renderer.domElement.parentElement) {
        renderer.domElement.parentElement.removeChild(renderer.domElement);
      }
      scene.clear();
    };
  }, [view, showGrid, gridSize, gridStep, isDark]);

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

    // RSSI scaling
    let rssiMin = Infinity;
    let rssiMax = -Infinity;
    for (let i = 0; i < count; i += 1) {
      const rssi = points[i].length >= 4 ? points[i][3] : points[i][2];
      if (rssi < rssiMin) rssiMin = rssi;
      if (rssi > rssiMax) rssiMax = rssi;
    }
    const denom = rssiMax - rssiMin || 1;

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
      const t = (rssi - rssiMin) / denom;
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
      size: 8,
      vertexColors: true,
      sizeAttenuation: true,
      map: circleTextureRef.current ?? undefined,
      transparent: true,
      alphaTest: 0.5,
    });

    const pts = new THREE.Points(geometry, material);
    pointsRef.current = pts;
    scene.add(pts);
  }, [points, mapAxes]);

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
      camera.position.set(cx + 1200, cy + 800, cz + 800);
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
      camera.position.set(0, 0, 2000);
      camera.lookAt(0, 0, 0);
      camera.updateProjectionMatrix();
    } else {
      camera.position.set(1200, 800, 800);
      camera.lookAt(0, 0, 0);
    }
    controls.target.set(0, 0, 0);
    controls.update();
  };

  useImperativeHandle(ref, () => ({ resetView, fitToPoints }), [points, mapAxes]);

  return <div ref={containerRef} style={{ width, height }} />;
});

export default PointCloudThreeViewer;
