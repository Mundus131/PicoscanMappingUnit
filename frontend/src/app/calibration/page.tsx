
'use client';

import React, { useEffect, useRef, useState } from 'react';
import Layout from '@/components/layout/Layout';
import api from '@/services/api';
import PointCloudThreeViewer, { type PointCloudThreeViewerHandle } from '@/components/visualization/PointCloudThreeViewer';
import { Plus, Edit, Trash2, X, Eye, Wand2 } from 'lucide-react';

interface Device {
  device_id: string;
  name: string;
  ip_address: string;
  port: number;
  enabled: boolean;
  connection_status?: string;
  calibration: {
    translation: number[];
    rotation_deg: number[];
    scale: number;
  };
  frame_corner?: string | null;
  frame_position?: number[] | null;
  frame_rotation_deg?: number[] | null;
}

export default function CalibrationPage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [previewActive, setPreviewActive] = useState(false);
  const [previewPoints, setPreviewPoints] = useState<number[][]>([]);
  const [autoPreviewStarted, setAutoPreviewStarted] = useState(false);
  const [autoResult, setAutoResult] = useState<any>(null);
  const previewRef = useRef<PointCloudThreeViewerHandle | null>(null);

  // System configurator fields
  const [frameWidth, setFrameWidth] = useState(2.0); // meters
  const [frameHeight, setFrameHeight] = useState(1.2); // meters
  const [originMode, setOriginMode] = useState<'center' | 'bottom-left' | 'front-left'>('center');
  const [frameSaving, setFrameSaving] = useState(false);
  const [frameDirty, setFrameDirty] = useState(false);
  const [frameSavedAt, setFrameSavedAt] = useState<number | null>(null);
  const [motionMode, setMotionMode] = useState<'fixed' | 'encoder'>('fixed');
  const [fixedSpeed, setFixedSpeed] = useState(0.5);
  const [profilingDistance, setProfilingDistance] = useState(10);
  const [encoderWheelMode, setEncoderWheelMode] = useState<'diameter' | 'circumference'>('diameter');
  const [encoderWheelValue, setEncoderWheelValue] = useState(100);
  const [encoderRps, setEncoderRps] = useState(0);
  const [motionSaving, setMotionSaving] = useState(false);
  const [motionSavedAt, setMotionSavedAt] = useState<number | null>(null);
  const [pingMap, setPingMap] = useState<Record<string, boolean | null>>({});
  const [tdcEnabled, setTdcEnabled] = useState(false);
  const [tdcIp, setTdcIp] = useState('192.168.0.100');
  const [tdcPort, setTdcPort] = useState(8081);
  const [tdcLogin, setTdcLogin] = useState('admin');
  const [tdcPassword, setTdcPassword] = useState('Welcome1!');
  const [tdcTriggerInput, setTdcTriggerInput] = useState('DI1');
  const [tdcPollInterval, setTdcPollInterval] = useState(200);
  const [tdcEncoderPort, setTdcEncoderPort] = useState<'1' | '2' | '3' | '4'>('1');
  const [tdcStartDelayMode, setTdcStartDelayMode] = useState<'time' | 'distance'>('time');
  const [tdcStartDelayMs, setTdcStartDelayMs] = useState(0);
  const [tdcStartDelayMm, setTdcStartDelayMm] = useState(0);
  const [tdcStopDelayMode, setTdcStopDelayMode] = useState<'time' | 'distance'>('time');
  const [tdcStopDelayMs, setTdcStopDelayMs] = useState(0);
  const [tdcStopDelayMm, setTdcStopDelayMm] = useState(0);
  const [tdcStatus, setTdcStatus] = useState<any>(null);
  const [tdcSaving, setTdcSaving] = useState(false);
  const [tdcSavedAt, setTdcSavedAt] = useState<number | null>(null);

  const frameSizeMm = React.useMemo(() => ({
    width: frameWidth * 1000,
    height: frameHeight * 1000,
  }), [frameWidth, frameHeight]);

  // Device management (moved from Settings)
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isDetailsOpen, setIsDetailsOpen] = useState(false);
  const [detailsDevice, setDetailsDevice] = useState<Device | null>(null);
  const [editingDevice, setEditingDevice] = useState<Device | null>(null);
  const [manualOverrideMap, setManualOverrideMap] = useState<Record<string, boolean>>({});
  const [positionDirty, setPositionDirty] = useState(false);
  const [rotationDirty, setRotationDirty] = useState(false);
  const [formData, setFormData] = useState({
    device_id: '',
    name: '',
    ip_address: '',
    port: 2115,
    enabled: true,
    calibration: {
      translation: [0, 0, 0],
      rotation_deg: [0, 0, 0],
      scale: 1.0,
    },
    frame_corner: 'bottom-left',
    frame_position: [0, 0, 0],
    frame_rotation_deg: [0, 0, 0], // use Z only in UI
  });

  const getCornerPosition = (corner: string, origin: typeof originMode, w = frameWidth, h = frameHeight) => {
    const halfW = w / 2;
    const halfH = h / 2;
    if (origin === 'center') {
      switch (corner) {
        case 'top-left':
          return [-halfW, halfH, 0];
        case 'top-right':
          return [halfW, halfH, 0];
        case 'bottom-right':
          return [halfW, -halfH, 0];
        case 'bottom-left':
        default:
          return [-halfW, -halfH, 0];
      }
    }
    if (origin === 'front-left') {
      switch (corner) {
        case 'top-left':
          return [0, h, 0];
        case 'top-right':
          return [w, h, 0];
        case 'bottom-right':
          return [w, 0, 0];
        case 'bottom-left':
        default:
          return [0, 0, 0];
      }
    }
    // bottom-left origin
    switch (corner) {
      case 'top-left':
        return [0, h, 0];
      case 'top-right':
        return [w, h, 0];
      case 'bottom-right':
        return [w, 0, 0];
      case 'bottom-left':
      default:
        return [0, 0, 0];
    }
  };

  const getCornerRotation = (corner: string) => {
    switch (corner) {
      case 'top-left':
        return [0, 0, 45];
      case 'top-right':
        return [0, 0, 135];
      case 'bottom-left':
        return [0, 0, -45];
      case 'bottom-right':
        return [0, 0, -135];
      default:
        return [0, 0, 0];
    }
  };

  const layoutRef = useRef({ w: frameWidth, h: frameHeight, origin: originMode, ready: false });

  useEffect(() => {
    const prev = layoutRef.current;
    const curr = { w: frameWidth, h: frameHeight, origin: originMode, ready: true };
    if (prev.ready && prev.w === curr.w && prev.h === curr.h && prev.origin === curr.origin) {
      return;
    }
    if (!prev.ready) {
      layoutRef.current = curr;
      return;
    }

    const eps = 1e-3;
    const updates = devices
      .filter((d) => d.frame_corner)
      .map((d) => {
        const corner = d.frame_corner as string;
        const prevPos = getCornerPosition(corner, prev.origin, prev.w, prev.h);
        const currPos = getCornerPosition(corner, curr.origin, curr.w, curr.h);
        const currRot = getCornerRotation(corner);
        const p = d.frame_position || prevPos;
        const dx = Math.abs((p?.[0] ?? 0) - prevPos[0]);
        const dy = Math.abs((p?.[1] ?? 0) - prevPos[1]);
        const dz = Math.abs((p?.[2] ?? 0) - prevPos[2]);
        const isAtCorner = (dx + dy + dz) < eps;
        if (!isAtCorner) return null;

        const currPosMm = [currPos[0] * 1000, currPos[1] * 1000, currPos[2] * 1000];
        const currPosMmData = [currPosMm[0], currPosMm[2], currPosMm[1]];
        const currRotData = [0, currRot[2] ?? 0, 0];
        const payload = {
          frame_position: currPos,
          frame_rotation_deg: currRot,
          calibration: {
            ...(d.calibration || { scale: 1.0, translation: [0, 0, 0], rotation_deg: [0, 0, 0] }),
            translation: currPosMmData,
            rotation_deg: currRotData,
          },
        };
        return api.put(`/devices/${d.device_id}`, payload);
      })
      .filter(Boolean);

    layoutRef.current = curr;
    if (updates.length > 0) {
      Promise.allSettled(updates).then(() => loadDevices());
    }
  }, [frameWidth, frameHeight, originMode, devices]);

  const loadDevices = async () => {
    const res = await api.get('/devices/');
    setDevices(res.data || []);
  };

  const refreshPing = async (ids: string[]) => {
    try {
      const results = await Promise.all(
        ids.map(async (id) => {
          try {
            const res = await api.get(`/devices/${id}/ping`);
            return [id, !!res.data?.reachable] as const;
          } catch {
            return [id, false] as const;
          }
        })
      );
      const next: Record<string, boolean> = {};
      results.forEach(([id, ok]) => { next[id] = ok; });
      setPingMap(next);
    } catch {
      // ignore
    }
  };

  const loadFrameSettings = async () => {
    try {
      const res = await api.get('/calibration/frame-settings');
      if (res.data) {
        setFrameWidth(res.data.width_m ?? 2.0);
        setFrameHeight(res.data.height_m ?? 1.2);
        setOriginMode(res.data.origin_mode ?? 'center');
        setFrameDirty(false);
      }
    } catch (error) {
      // ignore
    }
  };

  const loadMotionSettings = async () => {
    try {
      const res = await api.get('/calibration/motion-settings');
      if (res.data) {
        setMotionMode(res.data.mode === 'encoder' ? 'encoder' : 'fixed');
        if (typeof res.data.fixed_speed_mps === 'number') {
          setFixedSpeed(res.data.fixed_speed_mps);
        }
        if (typeof res.data.profiling_distance_mm === 'number') {
          setProfilingDistance(res.data.profiling_distance_mm);
        }
        if (res.data.encoder_wheel_mode === 'circumference') {
          setEncoderWheelMode('circumference');
        } else {
          setEncoderWheelMode('diameter');
        }
        if (typeof res.data.encoder_wheel_value_mm === 'number') {
          setEncoderWheelValue(res.data.encoder_wheel_value_mm);
        }
        if (typeof res.data.encoder_rps === 'number') {
          setEncoderRps(res.data.encoder_rps);
        }
      }
    } catch (error) {
      // ignore
    }
  };

  const loadTdcSettings = async () => {
    try {
      const res = await api.get('/calibration/tdc-settings');
      if (res.data) {
        setTdcEnabled(!!res.data.enabled);
        setTdcIp(res.data.ip_address ?? '192.168.0.100');
        setTdcPort(Number(res.data.port ?? 8081));
        setTdcLogin(res.data.login ?? 'admin');
        setTdcPassword(res.data.password ?? 'Welcome1!');
        setTdcTriggerInput(res.data.trigger_input ?? 'DI1');
        setTdcPollInterval(Number(res.data.poll_interval_ms ?? 200));
        setTdcEncoderPort((res.data.encoder_port ?? '1') as '1' | '2' | '3' | '4');
        setTdcStartDelayMode(res.data.start_delay_mode === 'distance' ? 'distance' : 'time');
        setTdcStartDelayMs(Number(res.data.start_delay_ms ?? 0));
        setTdcStartDelayMm(Number(res.data.start_delay_mm ?? 0));
        setTdcStopDelayMode(res.data.stop_delay_mode === 'distance' ? 'distance' : 'time');
        setTdcStopDelayMs(Number(res.data.stop_delay_ms ?? 0));
        setTdcStopDelayMm(Number(res.data.stop_delay_mm ?? 0));
      }
    } catch (error) {
      // ignore
    }
  };

  const loadTdcStatus = async () => {
    try {
      const res = await api.get('/tdc/status');
      setTdcStatus(res.data || null);
    } catch {
      setTdcStatus(null);
    }
  };

  async function handleSaveFrameSettings() {
    setFrameSaving(true);
    try {
      await api.put('/calibration/frame-settings', {
        width_m: frameWidth,
        height_m: frameHeight,
        origin_mode: originMode,
      });
      setFrameDirty(false);
      setFrameSavedAt(Date.now());
    } finally {
      setFrameSaving(false);
    }
  }

  async function handleSaveMotionSettings() {
    setMotionSaving(true);
    try {
      await api.put('/calibration/motion-settings', {
        mode: motionMode,
        fixed_speed_mps: motionMode === 'fixed' ? fixedSpeed : null,
        profiling_distance_mm: profilingDistance,
        encoder_wheel_mode: encoderWheelMode,
        encoder_wheel_value_mm: encoderWheelValue,
        encoder_rps: encoderRps,
      });
      setMotionSavedAt(Date.now());
    } finally {
      setMotionSaving(false);
    }
  }

  async function handleSaveTdcSettings() {
    setTdcSaving(true);
    try {
      await api.put('/calibration/tdc-settings', {
        enabled: tdcEnabled,
        ip_address: tdcIp,
        port: tdcPort,
        login: tdcLogin,
        password: tdcPassword,
        trigger_input: tdcTriggerInput,
        poll_interval_ms: tdcPollInterval,
        encoder_port: tdcEncoderPort,
        start_delay_mode: tdcStartDelayMode,
        start_delay_ms: tdcStartDelayMs,
        start_delay_mm: tdcStartDelayMm,
        stop_delay_mode: tdcStopDelayMode,
        stop_delay_ms: tdcStopDelayMs,
        stop_delay_mm: tdcStopDelayMm,
      });
      setTdcSavedAt(Date.now());
    } finally {
      setTdcSaving(false);
    }
  }

  useEffect(() => {
    loadDevices();
    loadFrameSettings();
    loadMotionSettings();
    loadTdcSettings();
    loadTdcStatus();
  }, []);

  useEffect(() => {
    if (devices.length === 0) return;
    const ids = devices.map((d) => d.device_id);
    refreshPing(ids);
    const interval = setInterval(() => refreshPing(ids), 3000);
    return () => clearInterval(interval);
  }, [devices]);

  useEffect(() => {
    const interval = setInterval(() => {
      loadTdcStatus();
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!frameDirty) return;
    const timer = setTimeout(() => {
      handleSaveFrameSettings();
    }, 700);
    return () => clearTimeout(timer);
  }, [frameWidth, frameHeight, originMode, frameDirty]);

  useEffect(() => {
    if (autoPreviewStarted) return;
    if (devices.length === 0) return;
    if (selectedIds.length > 0) return;
    const enabled = devices.filter((d) => d.enabled).map((d) => d.device_id);
    const initial = enabled.length > 0 ? enabled : [devices[0].device_id];
    setSelectedIds(initial);
    setPreviewActive(true);
    setAutoPreviewStarted(true);
  }, [autoPreviewStarted, devices, selectedIds]);

  useEffect(() => {
    if (!previewActive || selectedIds.length === 0) return;

    const fetchPreview = async () => {
      const res = await api.get(`/calibration/preview`, {
        params: { device_ids: selectedIds.join(','), max_points: 20000 },
      });
      setPreviewPoints(res.data?.points || []);
    };

    fetchPreview();
    const interval = setInterval(fetchPreview, 800);
    return () => clearInterval(interval);
  }, [previewActive, selectedIds]);

  // Disable auto reset/fit for live preview (manual control only)

  const handleOpenModal = (device?: Device) => {
    if (device) {
      setEditingDevice(device);
      setFormData({
        ...device,
        frame_corner: device.frame_corner || 'bottom-left',
        frame_position: device.frame_position || [0, 0, 0],
        frame_rotation_deg: device.frame_rotation_deg || [0, 0, 0],
      });
    } else {
      setEditingDevice(null);
      setFormData({
        device_id: `picoscan_${Date.now()}`,
        name: '',
        ip_address: '',
        port: 2115,
        enabled: true,
        calibration: {
          translation: [0, 0, 0],
          rotation_deg: [0, 0, 0],
          scale: 1.0,
        },
        frame_corner: 'bottom-left',
        frame_position: [0, 0, 0],
        frame_rotation_deg: [0, 0, 0],
      });
    }
    setPositionDirty(false);
    setRotationDirty(false);
    setIsModalOpen(true);
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setEditingDevice(null);
  };

  const handleSaveDevice = async () => {
    try {
      const translationMmFrame = formData.frame_position.map((v) => v * 1000);
      const translationMmData = [translationMmFrame[0], translationMmFrame[2], translationMmFrame[1]];
      const rotZ = formData.frame_rotation_deg[2] ?? 0;
      const rotData = [0, rotZ, 0];
      const payload = {
        ...formData,
        calibration: {
          ...(formData.calibration || { scale: 1.0, translation: [0, 0, 0], rotation_deg: [0, 0, 0] }),
          translation: translationMmData,
          rotation_deg: rotData,
        },
      };
      if (editingDevice) {
        const { device_id, ...updateData } = payload;
        await api.put(`/devices/${device_id}`, updateData);
        setManualOverrideMap((prev) => ({
          ...prev,
          [device_id]: positionDirty || rotationDirty,
        }));
      } else {
        await api.post('/devices/', payload);
        setManualOverrideMap((prev) => ({
          ...prev,
          [payload.device_id]: positionDirty || rotationDirty,
        }));
      }
      await loadDevices();
      handleCloseModal();
    } catch (error) {
      console.error('Failed to save device:', error);
    }
  };

  const handleDeleteDevice = async (deviceId: string) => {
    if (!confirm('Delete this device?')) return;
    try {
      await api.delete(`/devices/${deviceId}`);
      await loadDevices();
    } catch (error) {
      console.error('Failed to delete device:', error);
    }
  };

  const openDetails = (device: Device) => {
    setDetailsDevice(device);
    setIsDetailsOpen(true);
  };

  const closeDetails = () => {
    setDetailsDevice(null);
    setIsDetailsOpen(false);
  };

  const runAutoCalibration = async () => {
    if (selectedIds.length === 0) return;
    setLoading(true);
    try {
      const res = await api.post('/calibration/auto', {
        device_ids: selectedIds,
        method: 'icp',
        max_iterations: 50,
      });
      setAutoResult(res.data);
      await loadDevices();
    } catch (error) {
      console.error('Auto-calibration failed:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-slate-900">System Configurator</h1>
            <p className="text-sm text-gray-500 mt-1">Universal acquisition layout for multi-device systems</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="btn-secondary"
              onClick={async () => {
                if (!confirm('Restart backend now?')) return;
                try {
                  await api.post('/system/restart');
                } catch {
                  // ignore
                }
              }}
            >
              Restart Backend
            </button>
            <button className="btn-primary" onClick={() => handleOpenModal()}>
              <Plus size={16} />
              Add Device
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[1.1fr_0.9fr] gap-6">
          <div className="space-y-6">
            <div className="card">
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-semibold text-slate-900">Frame Layout</h2>
              <div className="text-xs text-gray-500">Gantry with corner-mounted LiDARs</div>
            </div>
            <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="text-xs text-gray-600">Frame width (m)</label>
                <input
                  className="input mt-1"
                  type="number"
                  step="0.1"
                  value={frameWidth}
                  onChange={(e) => {
                    setFrameWidth(parseFloat(e.target.value));
                    setFrameDirty(true);
                  }}
                />
              </div>
              <div>
                <label className="text-xs text-gray-600">Frame height (m)</label>
                <input
                  className="input mt-1"
                  type="number"
                  step="0.1"
                  value={frameHeight}
                  onChange={(e) => {
                    setFrameHeight(parseFloat(e.target.value));
                    setFrameDirty(true);
                  }}
                />
              </div>
              <div>
                <label className="text-xs text-gray-600">Coordinate origin</label>
                <select
                  className="input mt-1"
                  value={originMode}
                  onChange={(e) => {
                    setOriginMode(e.target.value as typeof originMode);
                    setFrameDirty(true);
                  }}
                >
                  <option value="center">Center of frame (recommended)</option>
                  <option value="bottom-left">Bottom-left corner</option>
                  <option value="front-left">Front-left corner</option>
                </select>
              </div>
            </div>
            <div className="mt-3 flex justify-end">
              <button className="btn-secondary" onClick={handleSaveFrameSettings} disabled={frameSaving}>
                {frameSaving ? 'Saving...' : 'Save Frame Settings'}
              </button>
            </div>
            {frameSavedAt && (
              <div className="mt-2 text-xs text-gray-500 text-right">
                Saved {new Date(frameSavedAt).toLocaleTimeString()}
              </div>
            )}

            <div className="mt-6 rounded-xl border border-gray-200 bg-white/70 dark:bg-gray-900/70 p-4">
              <div className="text-xs text-gray-500 mb-2">Preview (top view)</div>
              <div className="relative w-full aspect-[16/9] bg-gradient-to-br from-gray-50 to-white dark:from-slate-950 dark:to-slate-900 rounded-xl border border-gray-200 dark:border-gray-800">
                {(() => {
                  const rect = { x: 40, y: 20, w: 320, h: 180 };
                  const cornersWorld = [
                    getCornerPosition('top-left', originMode),
                    getCornerPosition('top-right', originMode),
                    getCornerPosition('bottom-right', originMode),
                    getCornerPosition('bottom-left', originMode),
                  ];
                  const xs = cornersWorld.map((c) => c[0]);
                  const ys = cornersWorld.map((c) => c[1]);
                  const minX = Math.min(...xs);
                  const maxX = Math.max(...xs);
                  const minY = Math.min(...ys);
                  const maxY = Math.max(...ys);
                  const spanX = maxX - minX || 1;
                  const spanY = maxY - minY || 1;
                  const worldToSvg = (wx: number, wy: number) => {
                    const nx = (wx - minX) / spanX;
                    const ny = (maxY - wy) / spanY;
                    const x = rect.x + nx * rect.w;
                    const y = rect.y + ny * rect.h;
                    return { x, y, nx: x / 400, ny: y / 220 };
                  };
                  const originSvg = worldToSvg(0, 0);
                  return (
                    <>
                      <svg viewBox="0 0 400 220" className="w-full h-full">
                        <rect x="40" y="20" width="320" height="180" rx="10" fill="none" stroke="#94a3b8" strokeWidth="2" />
                        <circle cx="40" cy="20" r="6" fill="#3b82f6" />
                        <circle cx="360" cy="20" r="6" fill="#3b82f6" />
                        <circle cx="40" cy="200" r="6" fill="#3b82f6" />
                        <circle cx="360" cy="200" r="6" fill="#3b82f6" />
                        <line x1={originSvg.x} y1={originSvg.y} x2={originSvg.x + 60} y2={originSvg.y} stroke="#ef4444" strokeWidth="2" />
                        <line x1={originSvg.x} y1={originSvg.y} x2={originSvg.x} y2={originSvg.y - 60} stroke="#22c55e" strokeWidth="2" />
                        <text x={originSvg.x + 64} y={originSvg.y + 4} fontSize="10" fill="#ef4444">X</text>
                        <text x={originSvg.x + 4} y={originSvg.y - 64} fontSize="10" fill="#22c55e">Y</text>
                        <circle cx={originSvg.x} cy={originSvg.y} r="5" fill="#111827" />
                      </svg>
                      {devices.map((device) => {
                        const corner = device.frame_corner || 'bottom-left';
                        const manualOverride = manualOverrideMap[device.device_id];
                        const calPos = device.calibration?.translation || null;
                        const calRot = device.calibration?.rotation_deg || null;
                        const pos = manualOverride
                          ? (device.frame_position || calPos || getCornerPosition(corner, originMode))
                          : getCornerPosition(corner, originMode);
                        const rot = (device.frame_rotation_deg || calRot || getCornerRotation(corner));
                        const p = worldToSvg(pos[0], pos[1]);
                        const yawAdj = rot?.[2] ?? 0;
                        const center = 180;
                        const radius = 160;
                        const fovDeg = 90;
                        const half = fovDeg / 2;
                        const toRad = (deg: number) => (deg * Math.PI) / 180;
                        const p1 = {
                          x: center + radius * Math.cos(toRad(-half)),
                          y: center + radius * Math.sin(toRad(-half)),
                        };
                        const p2 = {
                          x: center + radius * Math.cos(toRad(half)),
                          y: center + radius * Math.sin(toRad(half)),
                        };
                        const sector = `M ${center} ${center} L ${p1.x.toFixed(2)} ${p1.y.toFixed(2)} A ${radius} ${radius} 0 0 1 ${p2.x.toFixed(2)} ${p2.y.toFixed(2)} Z`;
                        return (
                          <div
                            key={device.device_id}
                            className="absolute text-[10px] px-1 py-0.5 rounded bg-blue-600 text-white overflow-visible"
                            style={{ left: `${p.nx * 100}%`, top: `${p.ny * 100}%`, transform: 'translate(-50%, -50%)' }}
                          >
                            {device.name || device.device_id}
                            <svg className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2" width="360" height="360">
                              <g transform={`rotate(${yawAdj.toFixed(2)}, ${center}, ${center})`}>
                                <path d={sector} fill="rgba(249,115,22,0.35)" stroke="#ea580c" strokeWidth="1.5" />
                              </g>
                            </svg>
                          </div>
                        );
                      })}
                      <div className="absolute bottom-3 right-4 text-xs text-gray-500">
                        {frameWidth.toFixed(1)} m x {frameHeight.toFixed(1)} m
                      </div>
                    </>
                  );
                })()}
              </div>
              <div className="mt-3 text-xs text-gray-600">
                Suggested coordinate system: X to the right, Y up, Z out of plane.
              </div>
            </div>
          </div>

          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-xl font-semibold text-slate-900">Motion Mode</h2>
                <p className="text-xs text-gray-500 mt-1">Fixed speed or encoder-driven operation</p>
              </div>
              <button className="btn-secondary" onClick={handleSaveMotionSettings} disabled={motionSaving}>
                {motionSaving ? 'Saving...' : 'Save Motion Settings'}
              </button>
            </div>
            <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="text-xs text-gray-600">Mode</label>
                <select
                  className="input mt-1"
                  value={motionMode}
                  onChange={(e) => setMotionMode(e.target.value as 'fixed' | 'encoder')}
                >
                  <option value="fixed">Fixed speed</option>
                  <option value="encoder">Encoder</option>
                </select>
              </div>
              {motionMode === 'fixed' ? (
                <div>
                  <label className="text-xs text-gray-600">Fixed speed (m/s)</label>
                  <input
                    className="input mt-1"
                    type="number"
                    step="0.01"
                    value={fixedSpeed}
                    onChange={(e) => setFixedSpeed(parseFloat(e.target.value))}
                  />
                </div>
              ) : (
                <div className="flex items-end text-xs text-gray-500">
                  Encoder active — speed handled by encoder.
                </div>
              )}
              <div>
                <label className="text-xs text-gray-600">Profiling distance (mm)</label>
                <input
                  className="input mt-1"
                  type="number"
                  step="1"
                  value={profilingDistance}
                  onChange={(e) => setProfilingDistance(parseFloat(e.target.value))}
                />
              </div>
              {motionMode === 'encoder' && (
                <>
                  <div>
                    <label className="text-xs text-gray-600">Measuring wheel</label>
                    <select
                      className="input mt-1"
                      value={encoderWheelMode}
                      onChange={(e) => setEncoderWheelMode(e.target.value as 'diameter' | 'circumference')}
                    >
                      <option value="diameter">Diameter (mm)</option>
                      <option value="circumference">Circumference (mm)</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-gray-600">
                      {encoderWheelMode === 'diameter' ? 'Wheel diameter (mm)' : 'Wheel circumference (mm)'}
                    </label>
                    <input
                      className="input mt-1"
                      type="number"
                      step="1"
                      value={encoderWheelValue}
                      onChange={(e) => setEncoderWheelValue(parseFloat(e.target.value))}
                    />
                  </div>
                  <div>
                    <label className="text-xs text-gray-600">Encoder rotations per second (rps)</label>
                    <input
                      className="input mt-1"
                      type="number"
                      step="0.01"
                      value={encoderRps}
                      onChange={(e) => setEncoderRps(parseFloat(e.target.value))}
                    />
                  </div>
                  <div className="flex items-end text-xs text-gray-500">
                    Speed: {encoderRps > 0 && encoderWheelValue > 0
                      ? (
                        (() => {
                          const circ = encoderWheelMode === 'circumference'
                            ? encoderWheelValue / 1000
                            : (encoderWheelValue * Math.PI) / 1000;
                          return (encoderRps * circ).toFixed(3);
                        })()
                      )
                      : '0.000'} m/s
                  </div>
                </>
              )}
            </div>
            {motionSavedAt && (
              <div className="mt-2 text-xs text-gray-500 text-right">
                Saved {new Date(motionSavedAt).toLocaleTimeString()}
              </div>
            )}
          </div>

          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-xl font-semibold text-slate-900">Digital Trigger (TDC)</h2>
                <p className="text-xs text-gray-500 mt-1">Start/stop acquisition based on digital input edges</p>
              </div>
              <button className="btn-secondary" onClick={handleSaveTdcSettings} disabled={tdcSaving}>
                {tdcSaving ? 'Saving...' : 'Save TDC Settings'}
              </button>
            </div>
            <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="flex items-center gap-2">
                <input
                  id="tdc-enabled"
                  type="checkbox"
                  checked={tdcEnabled}
                  onChange={(e) => setTdcEnabled(e.target.checked)}
                />
                <label htmlFor="tdc-enabled" className="text-sm text-gray-700">Enable digital trigger</label>
              </div>
              <div />
              <div>
                <label className="text-xs text-gray-600">TDC IP address</label>
                <input
                  className="input mt-1"
                  value={tdcIp}
                  onChange={(e) => setTdcIp(e.target.value)}
                />
              </div>
              <div>
                <label className="text-xs text-gray-600">gRPC port</label>
                <input
                  className="input mt-1"
                  type="number"
                  value={tdcPort}
                  onChange={(e) => setTdcPort(parseInt(e.target.value))}
                />
              </div>
              <div>
                <label className="text-xs text-gray-600">Login</label>
                <input
                  className="input mt-1"
                  value={tdcLogin}
                  onChange={(e) => setTdcLogin(e.target.value)}
                />
              </div>
              <div>
                <label className="text-xs text-gray-600">Password</label>
                <input
                  className="input mt-1"
                  type="password"
                  value={tdcPassword}
                  onChange={(e) => setTdcPassword(e.target.value)}
                />
              </div>
              <div>
                <label className="text-xs text-gray-600">Trigger input name</label>
                <select
                  className="input mt-1"
                  value={tdcTriggerInput}
                  onChange={(e) => setTdcTriggerInput(e.target.value)}
                >
                  <option value="DI_A">DI_A</option>
                  <option value="DI_B">DI_B</option>
                  <option value="DI_C">DI_C</option>
                  <option value="DI_D">DI_D</option>
                  <option value="DIO_A">DIO_A</option>
                  <option value="DIO_B">DIO_B</option>
                  <option value="DIO_C">DIO_C</option>
                  <option value="DIO_D">DIO_D</option>
                  <option value="DI1">DI1</option>
                  <option value="DI2">DI2</option>
                  <option value="DI3">DI3</option>
                  <option value="DI4">DI4</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-600">Poll interval (ms)</label>
                <input
                  className="input mt-1"
                  type="number"
                  step="10"
                  value={tdcPollInterval}
                  onChange={(e) => setTdcPollInterval(parseInt(e.target.value))}
                />
              </div>
              <div>
                <label className="text-xs text-gray-600">Encoder port</label>
                <select
                  className="input mt-1"
                  value={tdcEncoderPort}
                  onChange={(e) => setTdcEncoderPort(e.target.value as '1' | '2' | '3' | '4')}
                >
                  <option value="1">Port 1</option>
                  <option value="2">Port 2</option>
                  <option value="3">Port 3</option>
                  <option value="4">Port 4</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-600">Start delay mode</label>
                <select
                  className="input mt-1"
                  value={tdcStartDelayMode}
                  onChange={(e) => setTdcStartDelayMode(e.target.value as 'time' | 'distance')}
                >
                  <option value="time">Time</option>
                  <option value="distance">Distance</option>
                </select>
              </div>
              {tdcStartDelayMode === 'time' ? (
                <div>
                  <label className="text-xs text-gray-600">Start delay (ms)</label>
                  <input
                    className="input mt-1"
                    type="number"
                    step="10"
                    value={tdcStartDelayMs}
                    onChange={(e) => setTdcStartDelayMs(parseFloat(e.target.value))}
                  />
                </div>
              ) : (
                <div>
                  <label className="text-xs text-gray-600">Start delay (mm)</label>
                  <input
                    className="input mt-1"
                    type="number"
                    step="1"
                    value={tdcStartDelayMm}
                    onChange={(e) => setTdcStartDelayMm(parseFloat(e.target.value))}
                  />
                </div>
              )}
              <div>
                <label className="text-xs text-gray-600">Stop delay mode</label>
                <select
                  className="input mt-1"
                  value={tdcStopDelayMode}
                  onChange={(e) => setTdcStopDelayMode(e.target.value as 'time' | 'distance')}
                >
                  <option value="time">Time</option>
                  <option value="distance">Distance</option>
                </select>
              </div>
              {tdcStopDelayMode === 'time' ? (
                <div>
                  <label className="text-xs text-gray-600">Stop delay (ms)</label>
                  <input
                    className="input mt-1"
                    type="number"
                    step="10"
                    value={tdcStopDelayMs}
                    onChange={(e) => setTdcStopDelayMs(parseFloat(e.target.value))}
                  />
                </div>
              ) : (
                <div>
                  <label className="text-xs text-gray-600">Stop delay (mm)</label>
                  <input
                    className="input mt-1"
                    type="number"
                    step="1"
                    value={tdcStopDelayMm}
                    onChange={(e) => setTdcStopDelayMm(parseFloat(e.target.value))}
                  />
                </div>
              )}
            </div>
            {tdcSavedAt && (
              <div className="mt-2 text-xs text-gray-500 text-right">
                Saved {new Date(tdcSavedAt).toLocaleTimeString()}
              </div>
            )}
            {tdcStatus && (
              <div className="mt-4 rounded-lg border border-gray-200 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-900/40 p-3 text-xs text-gray-600">
                <div className="flex items-center justify-between">
                  <div className="font-semibold text-gray-700 dark:text-gray-300">TDC Status</div>
                  <div className="text-[11px] text-gray-500">poll: {tdcStatus.poll_interval_ms ?? '-'} ms</div>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <div>grpc: {tdcStatus.grpc_available ? 'ok' : 'missing'}</div>
                  <div>token: {tdcStatus.token?.has_token ? 'ok' : 'no token'}</div>
                  <div>input: {tdcStatus.input_state === 2 ? 'HIGH' : tdcStatus.input_state === 1 ? 'LOW' : 'UNKNOWN'}</div>
                  <div>encoder port: {tdcStatus.encoder_port || '-'}</div>
                </div>
              </div>
            )}
          </div>

        </div>

          <div className="card">
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-semibold text-slate-900">Devices</h2>
              <span className="text-xs text-gray-500">{devices.length} devices</span>
            </div>
              <div className="mt-4">
                {devices.length === 0 ? (
                  <div className="text-sm text-gray-500">No devices configured yet.</div>
                ) : (
                  <div className="table-container">
                    <div
                      className="table-header text-xs font-semibold text-gray-600"
                      style={{ display: 'grid', gridTemplateColumns: '2fr 1.2fr 0.7fr 0.9fr 0.9fr 0.7fr', columnGap: '0.5rem', alignItems: 'center' }}
                    >
                      <div>Name</div>
                      <div>IP</div>
                      <div>Port</div>
                      <div>Corner</div>
                      <div>Ping</div>
                      <div className="text-right">Actions</div>
                    </div>
                    {devices.map((device) => {
                      const ping = pingMap[device.device_id];
                      return (
                        <div
                          key={device.device_id}
                          className="table-row"
                          style={{ display: 'grid', gridTemplateColumns: '2fr 1.2fr 0.7fr 0.9fr 0.9fr 0.7fr', columnGap: '0.5rem', alignItems: 'center' }}
                        >
                          <div className="min-w-0">
                            <div className="font-medium text-slate-900 truncate">{device.name || device.device_id}</div>
                            <div className="text-xs text-gray-500 truncate">{device.device_id}</div>
                          </div>
                          <div className="font-mono text-xs text-gray-700 truncate">{device.ip_address}</div>
                          <div className="font-mono text-xs text-gray-700">{device.port}</div>
                          <div className="text-xs text-gray-600 truncate">{device.frame_corner || '-'}</div>
                          <div className="flex items-center gap-2 text-xs text-gray-600">
                            <span
                              className={`h-2.5 w-2.5 rounded-full ${
                                ping === undefined ? 'bg-amber-400' : ping ? 'bg-emerald-400' : 'bg-rose-400'
                              }`}
                            />
                            {ping === undefined ? 'checking' : ping ? 'reachable' : 'no ping'}
                          </div>
                          <div className="flex gap-2 justify-end">
                            <button className="btn-secondary px-2 py-2" onClick={() => handleOpenModal(device)} aria-label="Edit">
                              <Edit size={14} />
                            </button>
                            <button className="btn-danger px-2 py-2" onClick={() => handleDeleteDevice(device.device_id)} aria-label="Delete">
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

            <div className="mt-4">
              <label className="text-xs text-gray-600">Select devices for preview</label>
              <select
                className="input mt-1"
                multiple
                value={selectedIds}
                onChange={(e) => {
                  const opts = Array.from(e.target.selectedOptions).map((o) => o.value);
                  setSelectedIds(opts);
                }}
              >
                {devices.map((d) => (
                  <option key={d.device_id} value={d.device_id}>
                    {d.name || d.device_id}
                  </option>
                ))}
              </select>
              <div className="mt-3 flex gap-2">
                <button className="btn-primary" onClick={runAutoCalibration} disabled={loading || selectedIds.length === 0}>
                  <Wand2 size={14} />
                  Auto-Calibration (ICP)
                </button>
                <button className="btn-secondary" onClick={() => setPreviewActive((v) => !v)} disabled={selectedIds.length === 0}>
                  {previewActive ? 'Stop Preview' : 'Live Preview'}
                </button>
              </div>
              {autoResult && (
                <div className="mt-3 text-xs text-gray-600 bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-800 rounded-lg p-3">
                  <div className="font-semibold text-gray-700 dark:text-gray-300 mb-3">Auto-calibration result</div>
                  <div className="grid grid-cols-1 gap-2">
                    {(autoResult.results || []).map((r: any) => (
                      <div key={r.device_id} className="flex items-center justify-between bg-white/70 dark:bg-gray-900/60 rounded-md px-3 py-2 border border-gray-200 dark:border-gray-800">
                        <div className="text-xs font-semibold text-gray-700 dark:text-gray-200">{r.device_id}</div>
                        <div className="text-xs text-gray-500">score: {typeof r.score === 'number' ? r.score.toFixed(3) : '-'}</div>
                        <div className="text-xs text-gray-500">
                          t: {r.translation?.map((v: number) => v.toFixed(1)).join(', ') || '-'}
                        </div>
                        <div className="text-xs text-gray-500">
                          r: {r.rotation_deg?.map((v: number) => v.toFixed(2)).join(', ') || '-'}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {previewActive && (
          <div className="card">
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-semibold text-slate-900">Live Preview (Unified Frame)</h2>
              <div className="text-xs text-gray-500">{previewPoints.length} points</div>
            </div>
            <div className="mt-3" style={{ height: '500px' }}>
              <PointCloudThreeViewer
                ref={previewRef}
                points={previewPoints}
                mapAxes="xzy"
                view="2d"
                width="100%"
                height="100%"
                showOriginAxes
                originAxisSize={1000}
                showFrame
                frameSizeMm={frameSizeMm}
                frameOriginMode={originMode as any}
              />
            </div>
            <div className="mt-2 text-xs text-gray-500">Live preview uses X/Z plane for 2D view.</div>
          </div>
        )}
      </div>

      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={handleCloseModal} />
          <div className="relative w-full max-w-3xl mx-4 glass-card p-0 overflow-hidden">
            <div className="card-header flex items-center justify-between">
              <div className="text-lg font-semibold text-slate-900">
                {editingDevice ? 'Edit Device' : 'Add New Device'}
              </div>
              <button className="btn-secondary px-2 py-2" onClick={handleCloseModal}>
                <X size={16} />
              </button>
            </div>
            <div className="card-body space-y-6">
              <div className="space-y-4">
                <h3 className="font-semibold text-slate-900 text-sm">Basic Settings</h3>
                <input
                  className="input"
                  placeholder="Device Name"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                />
                {!editingDevice && (
                  <input
                    className="input"
                    placeholder="Device ID"
                    value={formData.device_id}
                    onChange={(e) => setFormData({ ...formData, device_id: e.target.value })}
                  />
                )}
                <input
                  className="input"
                  placeholder="IP Address"
                  value={formData.ip_address}
                  onChange={(e) => setFormData({ ...formData, ip_address: e.target.value })}
                  disabled={!!editingDevice}
                />
                <input
                  className="input"
                  type="number"
                  placeholder="Port"
                  value={formData.port.toString()}
                  onChange={(e) => setFormData({ ...formData, port: parseInt(e.target.value) })}
                  disabled={!!editingDevice}
                />
              </div>
              <div className="space-y-4">
                <h3 className="font-semibold text-slate-900 text-sm">Frame Placement</h3>
                <div>
                  <label className="text-xs text-gray-600">Corner assignment</label>
                  <select
                    className="input mt-1"
                    value={formData.frame_corner}
                    onChange={(e) => {
                      const nextCorner = e.target.value;
                      const nextPos = getCornerPosition(nextCorner, originMode);
                      const nextRot = getCornerRotation(nextCorner);
                      setFormData({
                        ...formData,
                        frame_corner: nextCorner,
                        frame_position: nextPos,
                        frame_rotation_deg: nextRot,
                      });
                      setPositionDirty(false);
                      setRotationDirty(false);
                    }}
                  >
                    <option value="top-left">Top-left</option>
                    <option value="top-right">Top-right</option>
                    <option value="bottom-left">Bottom-left</option>
                    <option value="bottom-right">Bottom-right</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-gray-600">Manual position (X/Y meters)</label>
                  <button
                    className="ml-2 text-xs text-blue-600 hover:text-blue-700"
                    type="button"
                    onClick={() => {
                      const nextPos = getCornerPosition(formData.frame_corner, originMode);
                      const nextRot = getCornerRotation(formData.frame_corner);
                      setFormData({ ...formData, frame_position: nextPos, frame_rotation_deg: nextRot });
                      setPositionDirty(false);
                      setRotationDirty(false);
                    }}
                  >
                    Snap to corner
                  </button>
                  <div className="grid grid-cols-2 gap-2 mt-1">
                    <input
                      className="input"
                      type="number"
                      value={formData.frame_position[0]}
                      onChange={(e) => {
                        setPositionDirty(true);
                        setFormData({ ...formData, frame_position: [parseFloat(e.target.value), formData.frame_position[1], formData.frame_position[2]] });
                      }}
                    />
                    <input
                      className="input"
                      type="number"
                      value={formData.frame_position[1]}
                      onChange={(e) => {
                        setPositionDirty(true);
                        setFormData({ ...formData, frame_position: [formData.frame_position[0], parseFloat(e.target.value), formData.frame_position[2]] });
                      }}
                    />
                  </div>
                </div>
                <div>
                  <label className="text-xs text-gray-600">Manual rotation (Z deg)</label>
                  <div className="grid grid-cols-1 gap-2 mt-1">
                    <input
                      className="input"
                      type="number"
                      value={formData.frame_rotation_deg[2]}
                      onChange={(e) => {
                        setRotationDirty(true);
                        setFormData({
                          ...formData,
                          frame_rotation_deg: [0, 0, parseFloat(e.target.value)],
                        });
                      }}
                    />
                    <div className="flex gap-2">
                      <button
                        className="btn-secondary px-2 py-1"
                        type="button"
                        onClick={() => {
                          setRotationDirty(true);
                          const v = (formData.frame_rotation_deg[2] ?? 0) - 5;
                          setFormData({ ...formData, frame_rotation_deg: [0, 0, v] });
                        }}
                      >
                        -5°
                      </button>
                      <button
                        className="btn-secondary px-2 py-1"
                        type="button"
                        onClick={() => {
                          setRotationDirty(true);
                          const v = (formData.frame_rotation_deg[2] ?? 0) - 1;
                          setFormData({ ...formData, frame_rotation_deg: [0, 0, v] });
                        }}
                      >
                        -1°
                      </button>
                      <button
                        className="btn-secondary px-2 py-1"
                        type="button"
                        onClick={() => {
                          setRotationDirty(true);
                          const v = (formData.frame_rotation_deg[2] ?? 0) + 1;
                          setFormData({ ...formData, frame_rotation_deg: [0, 0, v] });
                        }}
                      >
                        +1°
                      </button>
                      <button
                        className="btn-secondary px-2 py-1"
                        type="button"
                        onClick={() => {
                          setRotationDirty(true);
                          const v = (formData.frame_rotation_deg[2] ?? 0) + 5;
                          setFormData({ ...formData, frame_rotation_deg: [0, 0, v] });
                        }}
                      >
                        +5°
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div className="card-header flex items-center justify-end gap-2">
              <button className="btn-secondary" onClick={handleCloseModal}>Cancel</button>
              <button className="btn-primary" onClick={handleSaveDevice}>
                {editingDevice ? 'Update' : 'Create'} Device
              </button>
            </div>
          </div>
        </div>
      )}

      {isDetailsOpen && detailsDevice && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={closeDetails} />
          <div className="relative w-full max-w-2xl mx-4 glass-card p-0 overflow-hidden">
            <div className="card-header flex items-center justify-between">
              <div className="text-lg font-semibold text-slate-900">Device Details</div>
              <button className="btn-secondary px-2 py-2" onClick={closeDetails}>
                <X size={16} />
              </button>
            </div>
            <div className="card-body grid grid-cols-2 gap-4 text-sm text-gray-700">
              <div>
                <div className="text-xs text-gray-500">Device ID</div>
                <div className="font-mono">{detailsDevice.device_id}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Name</div>
                <div>{detailsDevice.name}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">IP Address</div>
                <div className="font-mono">{detailsDevice.ip_address}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Port</div>
                <div className="font-mono">{detailsDevice.port}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Corner</div>
                <div>{detailsDevice.frame_corner || '-'}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Position (X/Y)</div>
                <div className="font-mono">
                  {(detailsDevice.frame_position || [0, 0, 0]).slice(0, 2).join(', ')}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Rotation (Z)</div>
                <div className="font-mono">
                  {(detailsDevice.frame_rotation_deg || [0, 0, 0])[2]}
                </div>
              </div>
              <div className="col-span-2">
                <div className="text-xs text-gray-500">Calibration</div>
                <pre className="bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-800 rounded-lg p-3 text-xs overflow-auto">
{JSON.stringify(detailsDevice.calibration, null, 2)}
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}

    </Layout>

  );

}
