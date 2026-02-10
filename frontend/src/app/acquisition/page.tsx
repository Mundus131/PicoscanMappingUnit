'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import Layout from '@/components/layout/Layout';
import api from '@/services/api';
import PointCloudThreeViewer, { type PointCloudThreeViewerHandle } from '@/components/visualization/PointCloudThreeViewer';
import { Play, Square, RefreshCw } from 'lucide-react';

interface TriggerStatus {
  recording: boolean;
  distance_mm: number;
  speed_mps: number | null;
  profiling_distance_mm: number | null;
  profiles_count?: number;
  points_count: number;
  last_update_ts: number | null;
}

export default function AcquisitionPage() {
  const [status, setStatus] = useState<TriggerStatus>({
    recording: false,
    distance_mm: 0,
    speed_mps: null,
    profiling_distance_mm: null,
    profiles_count: 0,
    points_count: 0,
    last_update_ts: null,
  });
  const [points, setPoints] = useState<number[][]>([]);
  const [loading, setLoading] = useState(false);
  const [fullCloud, setFullCloud] = useState(false);
  const [viewerKey, setViewerKey] = useState(0);
  const [hoverPoint, setHoverPoint] = useState<number[] | null>(null);
  const [showFrame, setShowFrame] = useState(true);
  const [frameSize, setFrameSize] = useState<{ width: number; height: number } | null>(null);
  const [frameOriginMode, setFrameOriginMode] = useState<string>('center');
  const [showDevicesOnFrame, setShowDevicesOnFrame] = useState(false);
  const [devices, setDevices] = useState<any[]>([]);
  const viewerRef = useRef<PointCloudThreeViewerHandle | null>(null);
  const fitOnceRef = useRef(false);

  const fetchStatus = async () => {
    const res = await api.get('/acquisition/trigger/status');
    setStatus(res.data);
  };

  const fetchDevices = async () => {
    try {
      const res = await api.get('/devices/');
      setDevices(res.data || []);
    } catch {
      // ignore
    }
  };

  const normalizeOrigin = (value: string) => value.replace('_', '-');

  const fetchFrameSettings = async () => {
    try {
      const res = await api.get('/calibration/frame-settings');
      if (res.data?.width_m && res.data?.height_m) {
        setFrameSize({
          width: res.data.width_m * 1000,
          height: res.data.height_m * 1000,
        });
      }
      if (res.data?.origin_mode) {
        setFrameOriginMode(normalizeOrigin(res.data.origin_mode));
      }
    } catch {
      // ignore
    }
  };

  const fetchLatest = async (full = fullCloud) => {
    const res = await api.get('/acquisition/trigger/latest-cloud', {
      params: { max_points: full ? 0 : 30000 },
    });
    setPoints(res.data?.points || []);
    fitOnceRef.current = false;
    setHoverPoint(null);
    setViewerKey((v) => v + 1);
  };

  useEffect(() => {
    fetchStatus();
    fetchLatest();
    fetchFrameSettings();
    fetchDevices();
    const interval = setInterval(() => {
      fetchStatus();
      if (showDevicesOnFrame) {
        fetchDevices();
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [showDevicesOnFrame]);

  useEffect(() => {
    if (points.length > 0 && !fitOnceRef.current) {
      fitOnceRef.current = true;
      setTimeout(() => {
        viewerRef.current?.resetView();
        viewerRef.current?.fitToPoints();
      }, 150);
    }
  }, [points, viewerKey]);

  const handleStart = async () => {
    setLoading(true);
    try {
      await api.post('/acquisition/trigger/start');
      setPoints([]);
      fitOnceRef.current = false;
      setHoverPoint(null);
      setViewerKey((v) => v + 1);
      await fetchStatus();
    } finally {
      setLoading(false);
    }
  };

  const handleStop = async () => {
    setLoading(true);
    try {
      await api.post('/acquisition/trigger/stop');
      await fetchStatus();
      await fetchLatest();
    } finally {
      setLoading(false);
    }
  };

  const speedLabel = status.speed_mps !== null
    ? `${status.speed_mps.toFixed(2)} m/s`
    : 'Encoder';

  const distanceM = status.distance_mm / 1000.0;

  const handleHover = (p: number[] | null) => {
    setHoverPoint(p);
  };

  const rssiStats = useMemo(() => {
    if (!points || points.length === 0) return null;
    let min = Infinity;
    let max = -Infinity;
    let has = false;
    for (const p of points) {
      if (p.length >= 4) {
        const r = p[3];
        if (r < min) min = r;
        if (r > max) max = r;
        has = true;
      }
    }
    return has ? { min, max } : null;
  }, [points]);

  const bounds = useMemo(() => {
    if (!points || points.length === 0) return null;
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    for (const p of points) {
      if (p[0] < minX) minX = p[0];
      if (p[1] < minY) minY = p[1];
      if (p[2] < minZ) minZ = p[2];
      if (p[0] > maxX) maxX = p[0];
      if (p[1] > maxY) maxY = p[1];
      if (p[2] > maxZ) maxZ = p[2];
    }
    return {
      min: { x: minX, y: minY, z: minZ },
      max: { x: maxX, y: maxY, z: maxZ },
      size: { x: maxX - minX, y: maxY - minY, z: maxZ - minZ },
    };
  }, [points]);

  const displayPoints = useMemo(() => points, [points]);

  const getCornerPosition = (corner: string, origin: string, w: number, h: number) => {
    const halfW = w / 2;
    const halfH = h / 2;
    if (origin === 'center') {
      switch (corner) {
        case 'top-left':
        case 'top_left':
          return [-halfW, halfH];
        case 'top-right':
        case 'top_right':
          return [halfW, halfH];
        case 'bottom-right':
        case 'bottom_right':
          return [halfW, -halfH];
        case 'bottom-left':
        case 'bottom_left':
        default:
          return [-halfW, -halfH];
      }
    }
    if (origin === 'top-left' || origin === 'top_left') {
      switch (corner) {
        case 'top-left':
        case 'top_left':
          return [0, 0];
        case 'top-right':
        case 'top_right':
          return [w, 0];
        case 'bottom-right':
        case 'bottom_right':
          return [w, h];
        case 'bottom-left':
        case 'bottom_left':
        default:
          return [0, h];
      }
    }
    if (origin === 'top-right' || origin === 'top_right') {
      switch (corner) {
        case 'top-left':
        case 'top_left':
          return [-w, 0];
        case 'top-right':
        case 'top_right':
          return [0, 0];
        case 'bottom-right':
        case 'bottom_right':
          return [0, h];
        case 'bottom-left':
        case 'bottom_left':
        default:
          return [-w, h];
      }
    }
    if (origin === 'bottom-right' || origin === 'bottom_right') {
      switch (corner) {
        case 'top-left':
        case 'top_left':
          return [-w, -h];
        case 'top-right':
        case 'top_right':
          return [0, -h];
        case 'bottom-right':
        case 'bottom_right':
          return [0, 0];
        case 'bottom-left':
        case 'bottom_left':
        default:
          return [-w, 0];
      }
    }
    // bottom-left/front-left default
    switch (corner) {
      case 'top-left':
      case 'top_left':
        return [0, h];
      case 'top-right':
      case 'top_right':
        return [w, h];
      case 'bottom-right':
      case 'bottom_right':
        return [w, 0];
      case 'bottom-left':
      case 'bottom_left':
      default:
        return [0, 0];
    }
  };

  const deviceMarkers = useMemo(() => {
    if (!showDevicesOnFrame || !frameSize) return [];
    const rangeMm = Math.min(frameSize.width, frameSize.height) * 0.75;
    return devices.map((d) => {
      const corner = d.frame_corner || 'bottom-left';
      let xMm: number;
      let zMm: number;
      if (Array.isArray(d.frame_position) && d.frame_position.length >= 2) {
        // frame_position is stored in meters -> convert to mm
        xMm = Number(d.frame_position[0]) * 1000;
        zMm = Number(d.frame_position[1]) * 1000;
      } else {
        const pos = getCornerPosition(corner, frameOriginMode, frameSize.width, frameSize.height);
        xMm = pos[0];
        zMm = pos[1];
      }
      const yawDeg = Array.isArray(d.frame_rotation_deg)
        ? Number(d.frame_rotation_deg[2] ?? 0)
        : Array.isArray(d.calibration?.rotation_deg)
          ? Number(d.calibration.rotation_deg[2] ?? 0)
          : 0;
      // Frame is drawn on XZ plane (y=0)
      return {
        position: [xMm, 0, zMm] as [number, number, number],
        color: 0xf97316,
        size: 35,
        label: d.name || d.device_id,
        yawDeg,
        fovDeg: 90,
        rangeMm,
      };
    });
  }, [showDevicesOnFrame, devices, frameSize, frameOriginMode]);
  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-slate-900">Acquisition</h1>
            <p className="text-sm text-gray-500 mt-1">Trigger-based profile recording and 3D reconstruction</p>
          </div>
          <div className="flex items-center gap-2">
            <button className="btn-secondary" onClick={() => { fetchStatus(); fetchLatest(); }}>
              <RefreshCw className="h-4 w-4" />
              Refresh
            </button>
            {status.recording ? (
              <button className="btn-danger" onClick={handleStop} disabled={loading}>
                <Square className="h-4 w-4" />
                Stop
              </button>
            ) : (
              <button className="btn-success" onClick={handleStart} disabled={loading}>
                <Play className="h-4 w-4" />
                Start
              </button>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[1.35fr_0.65fr] gap-6">
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-xl font-semibold text-slate-900">Latest 3D Capture</h2>
              </div>
              <div className="flex items-center gap-2 text-xs text-gray-500">
                <span>{status.points_count} points</span>
                <label className="flex items-center gap-2 text-[11px] text-gray-500">
                  <input
                    type="checkbox"
                    checked={fullCloud}
                    onChange={(e) => setFullCloud(e.target.checked)}
                  />
                  Full cloud
                </label>
                <button
                  className="btn-secondary px-2 py-1"
                  onClick={() => fetchLatest(true)}
                  disabled={loading || status.recording}
                >
                  Load full
                </button>
              </div>
            </div>
            <div className="mt-4 relative" style={{ height: 760 }}>
                <PointCloudThreeViewer
                  key={viewerKey}
                  ref={viewerRef}
                  points={displayPoints}
                  view="3d"
                  mapAxes="xyz"
                  yHorizontal
                  width="100%"
                height="100%"
                showGrid
                gridSize={12000}
                gridStep={1000}
                colorScaleMode={rssiStats ? 'rssi100' : 'auto'}
                onHoverPoint={handleHover}
                showOriginAxes
                originAxisSize={1000}
                showFrame={showFrame}
                frameSizeMm={frameSize}
                frameOriginMode={frameOriginMode}
                framePlane="xz"
                markers={deviceMarkers}
              />
              {hoverPoint && (
                <div className="absolute bottom-4 right-4 rounded-md bg-black/70 px-3 py-2 text-xs text-white">
                  <div className="font-semibold mb-1">Point data</div>
                  <div>x: {hoverPoint[0]?.toFixed(2)}</div>
                  <div>y: {hoverPoint[1]?.toFixed(2)}</div>
                  <div>z: {hoverPoint[2]?.toFixed(2)}</div>
                  <div>rssi: {hoverPoint.length >= 4 ? hoverPoint[3]?.toFixed(1) : 'n/a'}</div>
                  <div className="text-[10px] text-gray-300 mt-1">Hover or click a point</div>
                </div>
              )}
            </div>
            {bounds && (
              <div className="mt-3 grid grid-cols-2 gap-3 text-xs text-gray-500">
                <div>
                  <div className="font-semibold text-gray-400">Bounds min</div>
                  x: {bounds.min.x.toFixed(2)} y: {bounds.min.y.toFixed(2)} z: {bounds.min.z.toFixed(2)}
                </div>
                <div>
                  <div className="font-semibold text-gray-400">Bounds max</div>
                  x: {bounds.max.x.toFixed(2)} y: {bounds.max.y.toFixed(2)} z: {bounds.max.z.toFixed(2)}
                </div>
                <div>
                  <div className="font-semibold text-gray-400">Size</div>
                  x: {bounds.size.x.toFixed(2)} y: {bounds.size.y.toFixed(2)} z: {bounds.size.z.toFixed(2)}
                </div>
                <div>
                  <div className="font-semibold text-gray-400">Points</div>
                  {points.length}
                </div>
              </div>
            )}
          </div>

          <div className="space-y-6">
            <div className="card">
              <h2 className="text-xl font-semibold text-slate-900">Live Metrics</h2>
              <div className="mt-2 text-xs text-gray-500">
                axes: frame plane X/Z, points mapAxes xzy
              </div>
            <div className="mt-4 grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs text-gray-500">Recording</p>
                  <p className="text-2xl font-semibold text-slate-900">{status.recording ? 'ON' : 'OFF'}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Speed</p>
                  <p className="text-2xl font-semibold text-slate-900">{speedLabel}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Distance</p>
                  <p className="text-2xl font-semibold text-slate-900">{distanceM.toFixed(3)} m</p>
                  <p className="text-xs text-gray-400">{status.distance_mm.toFixed(1)} mm</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Profiling distance</p>
                  <p className="text-2xl font-semibold text-slate-900">
                    {status.profiling_distance_mm !== null ? `${status.profiling_distance_mm} mm` : ''}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Profiles collected</p>
                  <p className="text-2xl font-semibold text-slate-900">{status.profiles_count ?? 0}</p>
                </div>
              </div>
              <div className="mt-4 text-xs text-gray-500">
                Last update: {status.last_update_ts ? new Date(status.last_update_ts * 1000).toLocaleTimeString() : ''}
              </div>
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={showFrame}
                      onChange={(e) => setShowFrame(e.target.checked)}
                    />
                    Show frame
                  </label>
                </div>
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={showDevicesOnFrame}
                      onChange={(e) => setShowDevicesOnFrame(e.target.checked)}
                    />
                    Show devices
                  </label>
                </div>
            </div>

            <div className="card">
              <h2 className="text-xl font-semibold text-slate-900">Trigger Control</h2>
              <p className="text-xs text-gray-500 mt-1">
                Start begins registering profiles with current calibration. Stop ends the session.
              </p>
              <div className="mt-4 flex gap-2">
                <button className="btn-success" onClick={handleStart} disabled={loading || status.recording}>
                  <Play className="h-4 w-4" />
                  Trigger Start
                </button>
                <button className="btn-danger" onClick={handleStop} disabled={loading || !status.recording}>
                  <Square className="h-4 w-4" />
                  Trigger Stop
                </button>
              </div>
            </div>

            <div className="card">
              <h2 className="text-xl font-semibold text-slate-900">Digital Input (coming soon)</h2>
              <p className="text-xs text-gray-500 mt-1">
                Next step: acquisition runs while the digital signal is high.
              </p>
            </div>
          </div>
        </div>
      </div>
    </Layout>
  );
}
