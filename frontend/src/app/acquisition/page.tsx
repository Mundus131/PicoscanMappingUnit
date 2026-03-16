'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import Layout from '@/components/layout/Layout';
import api from '@/services/api';
import PointCloudThreeViewer, { type PointCloudThreeViewerHandle } from '@/components/visualization/PointCloudThreeViewer';
import { SynInteropButton, SynInteropCheckbox } from '@/components/synergy/SynInterop';
import { Play, Square, RefreshCw } from 'lucide-react';

interface TriggerStatus {
  recording: boolean;
  distance_mm: number;
  speed_mps: number | null;
  encoder_rpm?: number | null;
  encoder_speed_mps?: number | null;
  profiling_distance_mm: number | null;
  profiles_count?: number;
  points_count: number;
  last_update_ts: number | null;
  tdc_input_state?: string;
  trigger_source?: string;
  devices_online?: number;
  devices_enabled?: number;
}

interface DeviceHealth {
  device_id: string;
  availability?: 'online' | 'offline' | 'unknown' | string;
  last_error?: string | null;
  latest_data_age_s?: number | null;
  data_rate_hz?: number | null;
  segments_per_scan_configured?: number | null;
  segments_per_scan_global_default?: number | null;
  segments_per_scan_runtime?: number | null;
  segments_per_scan_effective?: number | null;
  segments_per_scan_estimated?: number | null;
  segments_estimate_samples?: number;
  incomplete_frames_dropped?: number;
}

interface AvailabilityResponse {
  enabled_total: number;
  online_ids: string[];
  offline_ids: string[];
  unknown_ids: string[];
  health: Record<string, DeviceHealth>;
}

interface PerformanceAnalysisResponse {
  speed_limits?: {
    max_quality_mps?: number;
    recommended_mps?: number;
  };
  loop_stats?: {
    mean_update_ms?: number;
    p95_update_ms?: number;
  };
  validation_notes?: string[];
}

interface SegmentEstimateResponse {
  device_id: string;
  segments_per_scan_estimated?: number | null;
  configured_segments_per_scan?: number | null;
  samples?: number;
  applied?: boolean;
  note?: string;
}

interface AcquisitionDevice {
  device_id: string;
  name?: string;
  frame_corner?: string;
  frame_position?: number[];
  frame_rotation_deg?: number[];
  calibration?: { rotation_deg?: number[] };
}

type FrameOriginMode = 'center' | 'bottom-left' | 'front-left';

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
  const [frameOriginMode, setFrameOriginMode] = useState<FrameOriginMode>('center');
  const [colorMode, setColorMode] = useState<'rssi' | 'x' | 'y' | 'z'>('rssi');
  const [showDevicesOnFrame, setShowDevicesOnFrame] = useState(false);
  const [devices, setDevices] = useState<AcquisitionDevice[]>([]);
  const [availability, setAvailability] = useState<AvailabilityResponse | null>(null);
  const [performance, setPerformance] = useState<PerformanceAnalysisResponse | null>(null);
  const [segmentsDeviceId, setSegmentsDeviceId] = useState<string>('');
  const [estimateBusy, setEstimateBusy] = useState(false);
  const [estimateResult, setEstimateResult] = useState<SegmentEstimateResponse | null>(null);
  const viewerRef = useRef<PointCloudThreeViewerHandle | null>(null);
  const fitOnceRef = useRef(false);
  const statusStreamRef = useRef<EventSource | null>(null);
  const prevRecordingRef = useRef<boolean>(false);

  const fetchStatus = async () => {
    const res = await api.get('/acquisition/trigger/status');
    setStatus(res.data);
  };

  const fetchDevices = async () => {
    try {
      const res = await api.get('/devices/');
      const loaded = res.data || [];
      setDevices(loaded);
      if (!segmentsDeviceId && loaded.length > 0) {
        setSegmentsDeviceId(loaded[0].device_id);
      }
    } catch {
      // ignore
    }
  };

  const fetchAvailability = async () => {
    try {
      const res = await api.get('/acquisition/devices/availability');
      setAvailability(res.data || null);
    } catch {
      // ignore
    }
  };

  const fetchPerformance = async () => {
    try {
      const res = await api.get('/acquisition/trigger/performance-analysis');
      setPerformance(res.data || null);
    } catch {
      // ignore
    }
  };

  const normalizeOrigin = (value: string): FrameOriginMode => {
    const normalized = String(value || 'center').replace('_', '-').toLowerCase();
    if (normalized === 'bottom-left') return 'bottom-left';
    if (normalized === 'front-left') return 'front-left';
    return 'center';
  };

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
      params: {
        max_points: full ? 0 : 30000,
      },
    });
    setPoints(res.data?.points || []);
    fitOnceRef.current = false;
    setHoverPoint(null);
    setViewerKey((v) => v + 1);
  };

  const estimateSegments = async (autoApply = false) => {
    if (!segmentsDeviceId) return;
    setEstimateBusy(true);
    try {
      const res = await api.post(`/acquisition/segments/estimate/${segmentsDeviceId}`, null, {
        params: {
          sample_seconds: 4.0,
          min_samples: 8,
          auto_apply: autoApply,
        },
      });
      setEstimateResult(res.data || null);
      await fetchAvailability();
      await fetchDevices();
    } finally {
      setEstimateBusy(false);
    }
  };

  useEffect(() => {
    fetchStatus();
    fetchLatest();
    fetchFrameSettings();
    fetchDevices();
    fetchAvailability();
    fetchPerformance();
    const urlBase = (api.defaults.baseURL || '').replace(/\/+$/, '');
    const streamUrl = `${urlBase}/acquisition/trigger/status/stream`;
    try {
      const es = new EventSource(streamUrl);
      statusStreamRef.current = es;
      es.onmessage = (evt) => {
        try {
          const data = JSON.parse(evt.data);
          setStatus(data);
        } catch {
          // ignore
        }
      };
      es.onerror = () => {
        es.close();
        statusStreamRef.current = null;
      };
    } catch {
      // ignore
    }
    const interval = setInterval(() => {
      fetchStatus();
      fetchAvailability();
      fetchPerformance();
      if (showDevicesOnFrame) {
        fetchDevices();
      }
    }, 3000);
    return () => {
      if (statusStreamRef.current) {
        statusStreamRef.current.close();
        statusStreamRef.current = null;
      }
      clearInterval(interval);
    };
  }, [showDevicesOnFrame, fullCloud]);

  useEffect(() => {
    if (points.length > 0 && !fitOnceRef.current) {
      fitOnceRef.current = true;
      setTimeout(() => {
        viewerRef.current?.resetView();
        viewerRef.current?.fitToPoints();
      }, 150);
    }
  }, [points, viewerKey]);

  useEffect(() => {
    const wasRecording = prevRecordingRef.current;
    const isRecording = !!status.recording;
    if (wasRecording && !isRecording) {
      fetchLatest();
    }
    prevRecordingRef.current = isRecording;
  }, [status.recording, fullCloud]);

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

  const liveSpeedMps = status.encoder_speed_mps ?? status.speed_mps;
  const speedLabel = liveSpeedMps !== null && liveSpeedMps !== undefined
    ? `${liveSpeedMps.toFixed(2)} m/s`
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
    let minX = Infinity;
    let minY = Infinity;
    let minZ = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    let maxZ = -Infinity;
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

  const displayPoints = useMemo(
    () =>
      points.map((p) => {
        if (!Array.isArray(p) || p.length < 3) return p;
        const x = Number(p[0]);
        const y = Number(p[1]);
        const z = Number(p[2]);
        const transformed: number[] = [z, y, x];
        if (p.length > 3) transformed.push(...p.slice(3));
        return transformed;
      }),
    [points]
  );

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
    if (!showDevicesOnFrame) return [];
    const widthMm = frameSize?.width ?? 2000;
    const heightMm = frameSize?.height ?? 1200;
    const rangeMm = Math.min(widthMm, heightMm) * 0.75;
    return devices.map((d) => {
      const corner = d.frame_corner || 'bottom-left';
      let xMm: number;
      let zMm: number;
      if (Array.isArray(d.frame_position) && d.frame_position.length >= 2) {
        xMm = Number(d.frame_position[0]) * 1000;
        zMm = Number(d.frame_position[1]) * 1000;
      } else if (frameSize) {
        const pos = getCornerPosition(corner, frameOriginMode, frameSize.width, frameSize.height);
        xMm = pos[0];
        zMm = pos[1];
      } else {
        xMm = 0;
        zMm = 0;
      }
      const yawDeg = Array.isArray(d.frame_rotation_deg)
        ? Number(d.frame_rotation_deg[2] ?? 0)
        : Array.isArray(d.calibration?.rotation_deg)
          ? Number(d.calibration.rotation_deg[2] ?? 0)
          : 0;
      return {
        // Acquisition viewer swaps cloud axes to [z, y, x], so markers must follow it.
        position: [zMm, 0, xMm] as [number, number, number],
        color: 0xf97316,
        size: 35,
        label: d.name || d.device_id,
        yawDeg: -yawDeg - 90.0,
        fovDeg: 90,
        rangeMm,
      };
    });
  }, [showDevicesOnFrame, devices, frameSize, frameOriginMode]);

  const colorModeLabel = colorMode.toUpperCase();
  const viewerFrameSize = useMemo(() => {
    if (!frameSize) return null;
    // Keep frame overlay in the same visual basis as transformed cloud [z, y, x].
    return { width: frameSize.height, height: frameSize.width };
  }, [frameSize]);
  const selectedSegmentDeviceName = useMemo(() => {
    const found = (devices || []).find((d) => d.device_id === segmentsDeviceId);
    return found ? (found.name || found.device_id) : 'Select device';
  }, [devices, segmentsDeviceId]);

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-3xl font-bold text-slate-900">Acquisition</h1>
            <p className="mt-1 text-sm text-gray-500">Trigger-based profile recording and 3D reconstruction</p>
          </div>
          <div className="flex w-full flex-wrap items-center gap-2 md:w-auto">
            <syn-button onClick={() => { fetchStatus(); fetchLatest(); }}>
              <RefreshCw className="h-4 w-4" />
              Refresh
            </syn-button>
            {status.recording ? (
              <syn-button variant="danger" onClick={handleStop} disabled={loading}>
                <Square className="h-4 w-4" />
                Stop
              </syn-button>
            ) : (
              <syn-button variant="success" onClick={handleStart} disabled={loading}>
                <Play className="h-4 w-4" />
                Start
              </syn-button>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.35fr_0.65fr]">
          <syn-card className="app-card">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-xl font-semibold text-slate-900">Latest 3D Capture</h2>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                <span>{status.points_count} points</span>
                <SynInteropCheckbox checked={fullCloud} onToggle={setFullCloud}>
                  Full cloud
                </SynInteropCheckbox>
                <SynInteropButton size="small" onPress={() => fetchLatest(true)} disabled={loading || status.recording}>
                  Load full
                </SynInteropButton>
              </div>
            </div>

            <div className="relative mt-4 viewer-panel-height">
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
                gridStep={500}
                colorScaleMode={rssiStats ? 'rssi100' : 'auto'}
                colorMode={colorMode}
                onHoverPoint={handleHover}
                showOriginAxes
                originAxisSize={1000}
                showFrame={showFrame}
                frameSizeMm={viewerFrameSize}
                frameOriginMode={frameOriginMode}
                framePlane="xz"
                markers={deviceMarkers}
                showFloor={false}
              />

              <div className="absolute left-3 top-3 flex max-w-[calc(100%-88px)] items-center gap-2 rounded-md bg-black/40 px-2 py-1 text-xs text-white sm:left-4 sm:top-4">
                <span className="text-gray-200">Color</span>
                <syn-dropdown>
                  <syn-button slot="trigger" size="small" caret="">
                    {colorModeLabel}
                  </syn-button>
                  <syn-menu style={{ minWidth: 160 }}>
                    <syn-menu-item onClick={() => setColorMode('rssi')}>RSSI</syn-menu-item>
                    <syn-menu-item onClick={() => setColorMode('x')}>X</syn-menu-item>
                    <syn-menu-item onClick={() => setColorMode('y')}>Y</syn-menu-item>
                    <syn-menu-item onClick={() => setColorMode('z')}>Z</syn-menu-item>
                  </syn-menu>
                </syn-dropdown>
              </div>


              {hoverPoint && (
                <div className="absolute bottom-4 right-4 rounded-md bg-black/70 px-3 py-2 text-xs text-white">
                  <div className="mb-1 font-semibold">Point data</div>
                  <div>x: {hoverPoint[0]?.toFixed(2)}</div>
                  <div>y: {hoverPoint[1]?.toFixed(2)}</div>
                  <div>z: {hoverPoint[2]?.toFixed(2)}</div>
                  <div>rssi: {hoverPoint.length >= 4 ? hoverPoint[3]?.toFixed(1) : 'n/a'}</div>
                  <div className="mt-1 text-[10px] text-gray-300">Hover or click a point</div>
                </div>
              )}
            </div>

            {bounds && (
              <div className="mt-3 grid grid-cols-1 gap-3 text-xs text-gray-500 sm:grid-cols-2">
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

            <footer slot="footer">
              <small>Cloud uses full dataset for analysis and visualization.</small>
              <nav>
                <syn-button size="small" onClick={() => viewerRef.current?.resetView()}>Reset view</syn-button>
                <syn-button size="small" onClick={() => viewerRef.current?.fitToPoints()}>Fit view</syn-button>
              </nav>
            </footer>
          </syn-card>

          <div className="space-y-6">
            <syn-card className="app-card">
              <h2 className="text-xl font-semibold text-slate-900">Live Metrics</h2>
              <div className="mt-2 text-xs text-gray-500">axes: frame plane X/Z, points mapAxes xyz</div>

              <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <p className="text-xs text-gray-500">Recording</p>
                  <p className="text-2xl font-semibold text-slate-900">{status.recording ? 'ON' : 'OFF'}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Speed</p>
                  <p className="text-2xl font-semibold text-slate-900">{speedLabel}</p>
                  {status.encoder_rpm !== null && status.encoder_rpm !== undefined && (
                    <p className="text-xs text-gray-400">Encoder: {status.encoder_rpm.toFixed(1)} rpm</p>
                  )}
                </div>
                <div>
                  <p className="text-xs text-gray-500">Trigger input</p>
                  <div className="flex items-center gap-2">
                    <span
                      className={`h-2.5 w-2.5 rounded-full ${
                        status.tdc_input_state === 'HIGH'
                          ? 'bg-emerald-400'
                          : status.tdc_input_state === 'LOW'
                            ? 'bg-rose-400'
                            : 'bg-gray-400'
                      }`}
                    />
                    <p className="text-2xl font-semibold text-slate-900">{status.tdc_input_state || 'UNKNOWN'}</p>
                  </div>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Trigger source</p>
                  <p className="text-2xl font-semibold text-slate-900">{status.trigger_source || 'manual'}</p>
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
              {availability && (
                <div className="mt-2 text-xs text-gray-500">
                  Devices online: {availability.online_ids.length} / {availability.enabled_total}
                </div>
              )}
              <div className="mt-2 flex items-center gap-3 text-xs text-gray-500">
                <SynInteropCheckbox checked={showFrame} onToggle={setShowFrame}>
                  Show frame
                </SynInteropCheckbox>
                <SynInteropCheckbox checked={showDevicesOnFrame} onToggle={setShowDevicesOnFrame}>
                  Show devices
                </SynInteropCheckbox>
              </div>

              <footer slot="footer">
                <small>Runtime stream health and overlay controls.</small>
              </footer>
            </syn-card>

            <syn-card className="app-card">
              <h2 className="text-xl font-semibold text-slate-900">Device Stream Health & Segments</h2>
              <p className="mt-1 text-xs text-gray-500">
                Sprawdzenie online/offline i estymacja liczby segmentow przed akwizycja.
              </p>

              <div className="mt-3 grid grid-cols-1 gap-2">
                {(devices || []).map((d) => {
                  const h = availability?.health?.[d.device_id];
                  const st = h?.availability || 'unknown';
                  const stClass = st === 'online' ? 'text-emerald-700' : st === 'offline' ? 'text-rose-700' : 'text-amber-700';
                  return (
                    <div key={`health-${d.device_id}`} className="rounded-md border border-slate-200 px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <div className="truncate text-sm font-semibold text-slate-900">{d.name || d.device_id}</div>
                        <div className={`text-xs font-semibold uppercase ${stClass}`}>{st}</div>
                      </div>
                      <div className="mt-1 text-xs text-gray-500">
                        cfg(dev): {h?.segments_per_scan_configured ?? '-'} | cfg(global): {h?.segments_per_scan_global_default ?? '-'} | runtime: {h?.segments_per_scan_runtime ?? '-'}
                      </div>
                      <div className="text-xs text-gray-500">
                        effective: {h?.segments_per_scan_effective ?? '-'} | estimated: {h?.segments_per_scan_estimated ?? '-'} | samples: {h?.segments_estimate_samples ?? 0}
                      </div>
                      <div className="text-xs text-gray-500">
                        dropped incomplete frames: {h?.incomplete_frames_dropped ?? 0}
                      </div>
                      <div className="text-xs text-gray-500">
                        data age: {typeof h?.latest_data_age_s === 'number' ? `${h.latest_data_age_s.toFixed(2)} s` : '-'} | rate: {typeof h?.data_rate_hz === 'number' ? `${h.data_rate_hz.toFixed(1)} Hz` : '-'}
                      </div>
                      {h?.last_error && <div className="mt-1 break-all text-xs text-rose-600">err: {h.last_error}</div>}
                    </div>
                  );
                })}
              </div>

              <div className="mt-4 grid grid-cols-1 gap-3">
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-gray-500">Device for segment estimation</span>
                  <syn-dropdown>
                    <syn-button slot="trigger" caret="">{selectedSegmentDeviceName}</syn-button>
                    <syn-menu style={{ minWidth: 260 }}>
                      {(devices || []).map((d) => (
                        <syn-menu-item key={`segment-dev-${d.device_id}`} onClick={() => setSegmentsDeviceId(d.device_id)}>
                          {d.name || d.device_id}
                        </syn-menu-item>
                      ))}
                    </syn-menu>
                  </syn-dropdown>
                </label>

                <div className="flex flex-wrap gap-2">
                  <syn-button onClick={() => estimateSegments(false)} disabled={estimateBusy || !segmentsDeviceId}>
                    {estimateBusy ? 'Estimating...' : 'Estimate Segments'}
                  </syn-button>
                  <syn-button variant="filled" onClick={() => estimateSegments(true)} disabled={estimateBusy || !segmentsDeviceId}>
                    {estimateBusy ? 'Applying...' : 'Estimate & Apply'}
                  </syn-button>
                </div>

                {estimateResult && (
                  <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-gray-600">
                    device: {estimateResult.device_id} | est: {estimateResult.segments_per_scan_estimated ?? '-'} | cfg: {estimateResult.configured_segments_per_scan ?? '-'} | samples: {estimateResult.samples ?? 0} | applied: {estimateResult.applied ? 'yes' : 'no'}
                    {estimateResult.note && <div className="mt-1">{estimateResult.note}</div>}
                  </div>
                )}
              </div>

              {performance && (
                <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-gray-600">
                  speed max: {typeof performance.speed_limits?.max_quality_mps === 'number' ? `${performance.speed_limits.max_quality_mps.toFixed(3)} m/s` : '-'} | recommended: {typeof performance.speed_limits?.recommended_mps === 'number' ? `${performance.speed_limits.recommended_mps.toFixed(3)} m/s` : '-'}
                  <div className="mt-1">
                    loop mean/p95: {typeof performance.loop_stats?.mean_update_ms === 'number' ? `${performance.loop_stats.mean_update_ms.toFixed(1)} ms` : '-'} / {typeof performance.loop_stats?.p95_update_ms === 'number' ? `${performance.loop_stats.p95_update_ms.toFixed(1)} ms` : '-'}
                  </div>
                </div>
              )}

              <footer slot="footer">
                <small>Segment validation before acquisition startup.</small>
              </footer>
            </syn-card>
          </div>
        </div>
      </div>
    </Layout>
  );
}
