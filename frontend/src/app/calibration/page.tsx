
'use client';

import React, { useEffect, useRef, useState } from 'react';
import Layout from '@/components/layout/Layout';
import api from '@/services/api';
import PointCloudThreeViewer, { type PointCloudThreeViewerHandle } from '@/components/visualization/PointCloudThreeViewer';
import { Plus, Edit, Trash2, X, Wand2 } from 'lucide-react';

interface Device {
  device_id: string;
  name: string;
  ip_address: string;
  port: number;
  device_type?: 'picoscan' | 'lms4000';
  protocol?: string | null;
  format_type?: string | null;
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

interface AcquisitionLiveStatus {
  speed_mps: number | null;
  encoder_rpm?: number | null;
  encoder_speed_mps?: number | null;
}

type TriggerInputName = 'DI_A' | 'DI_B' | 'DI_C' | 'DI_D' | 'DIO_A' | 'DIO_B' | 'DIO_C' | 'DIO_D';
type AnalysisApp = 'log' | 'conveyor_object';

const TRIGGER_INPUT_OPTIONS: TriggerInputName[] = ['DI_A', 'DI_B', 'DI_C', 'DI_D', 'DIO_A', 'DIO_B', 'DIO_C', 'DIO_D'];

interface IoStateResponse {
  enabled: boolean;
  grpc_available: boolean;
  read_ts: number;
  states: Record<string, { state: number | null; label: string }>;
}

interface TdcStatusResponse {
  poll_interval_ms?: number | null;
  grpc_available?: boolean | null;
  token?: { has_token?: boolean | null } | null;
  input_state?: number | null;
  encoder_port?: string | null;
}

interface LiveAdjustment {
  x: number;
  y: number;
  z: number;
  yaw: number;
}

interface AutoCalibrationResultItem {
  device_id: string;
  translation: number[];
  rotation_deg: number[];
  scale: number;
  score?: number | null;
}

interface AutoCalibrationResponsePayload {
  method: string;
  saved?: boolean;
  results: AutoCalibrationResultItem[];
}

interface RegionRectNorm {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export default function CalibrationPage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [referenceDeviceId, setReferenceDeviceId] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [previewActive, setPreviewActive] = useState(false);
  const [previewPoints, setPreviewPoints] = useState<number[][]>([]);
  const [previewVisibleIds, setPreviewVisibleIds] = useState<string[]>([]);
  const [previewDeviceId, setPreviewDeviceId] = useState<string>('');
  const [liveAdjustments, setLiveAdjustments] = useState<Record<string, LiveAdjustment>>({});
  const liveAdjustSaveTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const [autoResult, setAutoResult] = useState<AutoCalibrationResponsePayload | null>(null);
  const previewRef = useRef<PointCloudThreeViewerHandle | null>(null);
  const previewFitDoneRef = useRef(false);
  const previewRequestInFlightRef = useRef(false);
  const previewPollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const previewViewportRef = useRef<HTMLDivElement | null>(null);
  const regionDragRef = useRef<{
    mode: 'move' | 'nw' | 'ne' | 'sw' | 'se';
    startX: number;
    startY: number;
    startRect: RegionRectNorm;
  } | null>(null);

  // System configurator fields
  const [frameWidth, setFrameWidth] = useState(2.0); // meters
  const [frameHeight, setFrameHeight] = useState(1.2); // meters
  const [originMode, setOriginMode] = useState<'center' | 'bottom-left' | 'front-left'>('center');
  const [clipPointsToFrame, setClipPointsToFrame] = useState(false);
  const [frameSaving, setFrameSaving] = useState(false);
  const [frameDirty, setFrameDirty] = useState(false);
  const [frameSavedAt, setFrameSavedAt] = useState<number | null>(null);
  const [frameSaveError, setFrameSaveError] = useState<string | null>(null);
  const [previewRefreshTick, setPreviewRefreshTick] = useState(0);
  const [autoCalibSaveResult, setAutoCalibSaveResult] = useState(false);
  const [previewAutoCalibrationEnabled, setPreviewAutoCalibrationEnabled] = useState(false);
  const [previewEdgeFilterEnabled, setPreviewEdgeFilterEnabled] = useState(false);
  const [previewEdgeCurvatureThreshold, setPreviewEdgeCurvatureThreshold] = useState(0.08);
  const [previewVoxelDenoiseEnabled, setPreviewVoxelDenoiseEnabled] = useState(false);
  const [previewVoxelCellMm, setPreviewVoxelCellMm] = useState(8);
  const [previewVoxelMinPointsPerCell, setPreviewVoxelMinPointsPerCell] = useState(3);
  const [previewVoxelKeepLargestComponent, setPreviewVoxelKeepLargestComponent] = useState(false);
  const [previewRegionEnabled, setPreviewRegionEnabled] = useState(false);
  const [previewRegionRect, setPreviewRegionRect] = useState<RegionRectNorm>({ x0: 0.2, y0: 0.15, x1: 0.8, y1: 0.85 });
  const [previewOrthogonalFilterEnabled, setPreviewOrthogonalFilterEnabled] = useState(false);
  const [previewOrthogonalToleranceDeg, setPreviewOrthogonalToleranceDeg] = useState(12);
  const [previewNoiseFilterEnabled, setPreviewNoiseFilterEnabled] = useState(false);
  const [previewNoiseFilterK, setPreviewNoiseFilterK] = useState(16);
  const [previewNoiseFilterStdRatio, setPreviewNoiseFilterStdRatio] = useState(1.2);
  const [autoCalibApplying, setAutoCalibApplying] = useState(false);
  const [autoCalibCandidateMap, setAutoCalibCandidateMap] = useState<Record<string, { translation: number[]; rotation_deg: number[]; scale: number }>>({});
  const [previewFilterSaving, setPreviewFilterSaving] = useState(false);
  const [previewFilterSavedAt, setPreviewFilterSavedAt] = useState<number | null>(null);
  const [motionMode, setMotionMode] = useState<'fixed' | 'encoder'>('fixed');
  const [fixedSpeed, setFixedSpeed] = useState(0.5);
  const [profilingDistance, setProfilingDistance] = useState(10);
  const [encoderWheelMode, setEncoderWheelMode] = useState<'diameter' | 'circumference'>('diameter');
  const [encoderWheelValue, setEncoderWheelValue] = useState(100);
  const [encoderRps, setEncoderRps] = useState(0);
  const [motionSaving, setMotionSaving] = useState(false);
  const [motionSavedAt, setMotionSavedAt] = useState<number | null>(null);
  const [analysisApp, setAnalysisApp] = useState<AnalysisApp>('log');
  const [logWindowProfiles, setLogWindowProfiles] = useState(10);
  const [logMinPoints, setLogMinPoints] = useState(50);
  const [convPlaneQuantile, setConvPlaneQuantile] = useState(0.35);
  const [convPlaneInlierMm, setConvPlaneInlierMm] = useState(8);
  const [convObjectMinHeightMm, setConvObjectMinHeightMm] = useState(8);
  const [convObjectMaxPoints, setConvObjectMaxPoints] = useState(60000);
  const [analysisSaving, setAnalysisSaving] = useState(false);
  const [analysisSavedAt, setAnalysisSavedAt] = useState<number | null>(null);
  const [pingMap, setPingMap] = useState<Record<string, boolean | null>>({});
  const [tdcEnabled, setTdcEnabled] = useState(false);
  const [tdcIp, setTdcIp] = useState('192.168.0.100');
  const [tdcPort, setTdcPort] = useState(8081);
  const [tdcLogin, setTdcLogin] = useState('admin');
  const [tdcPassword, setTdcPassword] = useState('Welcome1!');
  const [tdcTriggerInput, setTdcTriggerInput] = useState<TriggerInputName>('DI_A');
  const [tdcPollInterval, setTdcPollInterval] = useState(200);
  const [tdcEncoderPort, setTdcEncoderPort] = useState<'1' | '2' | '3' | '4'>('1');
  const [tdcStartDelayMode, setTdcStartDelayMode] = useState<'time' | 'distance'>('time');
  const [tdcStartDelayMs, setTdcStartDelayMs] = useState(0);
  const [tdcStartDelayMm, setTdcStartDelayMm] = useState(0);
  const [tdcStopDelayMode, setTdcStopDelayMode] = useState<'time' | 'distance'>('time');
  const [tdcStopDelayMs, setTdcStopDelayMs] = useState(0);
  const [tdcStopDelayMm, setTdcStopDelayMm] = useState(0);
  const [tdcStatus, setTdcStatus] = useState<TdcStatusResponse | null>(null);
  const [acquisitionLiveStatus, setAcquisitionLiveStatus] = useState<AcquisitionLiveStatus | null>(null);
  const [ioState, setIoState] = useState<IoStateResponse | null>(null);
  const [tdcSaving, setTdcSaving] = useState(false);
  const [tdcSavedAt, setTdcSavedAt] = useState<number | null>(null);

  const frameSizeMm = React.useMemo(() => ({
    width: frameWidth * 1000,
    height: frameHeight * 1000,
  }), [frameWidth, frameHeight]);

  const toFramePositionFromCalibration = (translation: number[] | undefined) => {
    const tx = Number(translation?.[0] ?? 0);
    const ty = Number(translation?.[1] ?? 0);
    const tz = Number(translation?.[2] ?? 0);
    return {
      x: tx / 1000.0,
      y: tz / 1000.0,
      z: ty / 1000.0,
    };
  };

  const normalizedRegionRect = React.useMemo(() => {
    const x0 = Math.max(0, Math.min(1, Math.min(previewRegionRect.x0, previewRegionRect.x1)));
    const x1 = Math.max(0, Math.min(1, Math.max(previewRegionRect.x0, previewRegionRect.x1)));
    const y0 = Math.max(0, Math.min(1, Math.min(previewRegionRect.y0, previewRegionRect.y1)));
    const y1 = Math.max(0, Math.min(1, Math.max(previewRegionRect.y0, previewRegionRect.y1)));
    return { x0, y0, x1, y1 };
  }, [previewRegionRect]);

  const previewRegionWorldBounds = React.useMemo(() => {
    const widthMm = frameWidth * 1000.0;
    const heightMm = frameHeight * 1000.0;
    const xMin = originMode === 'center' ? -widthMm / 2.0 : 0.0;
    const xMax = originMode === 'center' ? widthMm / 2.0 : widthMm;
    const zMin = originMode === 'center' ? -heightMm / 2.0 : 0.0;
    const zMax = originMode === 'center' ? heightMm / 2.0 : heightMm;
    const spanX = xMax - xMin;
    const spanZ = zMax - zMin;
    const nxToX = (nx: number) => xMin + nx * spanX;
    const nyToZ = (ny: number) => zMax - ny * spanZ;
    const aX = nxToX(normalizedRegionRect.x0);
    const bX = nxToX(normalizedRegionRect.x1);
    const aZ = nyToZ(normalizedRegionRect.y0);
    const bZ = nyToZ(normalizedRegionRect.y1);
    return {
      minX: Math.min(aX, bX),
      maxX: Math.max(aX, bX),
      minZ: Math.min(aZ, bZ),
      maxZ: Math.max(aZ, bZ),
    };
  }, [frameWidth, frameHeight, originMode, normalizedRegionRect]);

  // Device management (moved from Settings)
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isDetailsOpen, setIsDetailsOpen] = useState(false);
  const [detailsDevice, setDetailsDevice] = useState<Device | null>(null);
  const [editingDevice, setEditingDevice] = useState<Device | null>(null);
  const [formData, setFormData] = useState({
    device_id: '',
    name: '',
    ip_address: '',
    port: 2115,
    device_type: 'picoscan' as 'picoscan' | 'lms4000',
    protocol: 'udp',
    format_type: 'compact',
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

  const getLiveAdjustmentFromDevice = (device: Device): LiveAdjustment => {
    const pos = Array.isArray(device.frame_position) && device.frame_position.length >= 3
      ? device.frame_position
      : [0, 0, 0];
    const rot = Array.isArray(device.frame_rotation_deg) && device.frame_rotation_deg.length >= 3
      ? device.frame_rotation_deg
      : [0, 0, 0];
    return {
      x: Number(pos[0] ?? 0),
      y: Number(pos[1] ?? 0),
      z: Number(pos[2] ?? 0),
      yaw: Number(rot[2] ?? 0),
    };
  };

  const buildCalibrationPayloadFromFrame = (framePosition: number[], frameRotationDeg: number[]) => {
    const translationMmFrame = framePosition.map((v) => Number(v) * 1000);
    const translationMmData = [translationMmFrame[0], translationMmFrame[2], translationMmFrame[1]];
    const rotZ = Number(frameRotationDeg[2] ?? 0);
    const rotData = [0, rotZ, 0];
    return {
      translation: translationMmData,
      rotation_deg: rotData,
      scale: 1.0,
    };
  };

  const persistLiveAdjustment = async (deviceId: string, adjustment: LiveAdjustment) => {
    const device = devices.find((d) => d.device_id === deviceId);
    if (!device) return;
    const framePosition = [adjustment.x, adjustment.y, adjustment.z];
    const frameRotationDeg = [0, 0, adjustment.yaw];
    const baseCalibration = device.calibration || { translation: [0, 0, 0], rotation_deg: [0, 0, 0], scale: 1.0 };

    await api.put(`/devices/${deviceId}`, {
      frame_position: framePosition,
      frame_rotation_deg: frameRotationDeg,
      calibration: {
        ...baseCalibration,
        ...buildCalibrationPayloadFromFrame(framePosition, frameRotationDeg),
      },
    });
  };

  const handleLiveAdjustmentChange = (deviceId: string, patch: Partial<LiveAdjustment>) => {
    setLiveAdjustments((prev) => {
      const current = prev[deviceId] || { x: 0, y: 0, z: 0, yaw: 0 };
      const next = { ...current, ...patch };
      const nextMap = { ...prev, [deviceId]: next };

      if (liveAdjustSaveTimersRef.current[deviceId]) {
        clearTimeout(liveAdjustSaveTimersRef.current[deviceId]);
      }
      liveAdjustSaveTimersRef.current[deviceId] = setTimeout(async () => {
        try {
          await persistLiveAdjustment(deviceId, next);
        } catch (error) {
          console.error(`Failed to persist live adjustment for ${deviceId}:`, error);
        }
      }, 250);

      return nextMap;
    });
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
        setClipPointsToFrame(!!res.data.clip_points_to_frame);
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
        const triggerInput = String(res.data.trigger_input ?? 'DI_A').toUpperCase();
        setTdcTriggerInput(
          (TRIGGER_INPUT_OPTIONS.includes(triggerInput as TriggerInputName) ? triggerInput : 'DI_A') as TriggerInputName
        );
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

  const loadAnalysisSettings = async () => {
    try {
      const res = await api.get('/calibration/analysis-settings');
      if (res.data) {
        setAnalysisApp(res.data.active_app === 'conveyor_object' ? 'conveyor_object' : 'log');
        if (typeof res.data.log_window_profiles === 'number') setLogWindowProfiles(res.data.log_window_profiles);
        if (typeof res.data.log_min_points === 'number') setLogMinPoints(res.data.log_min_points);
        if (typeof res.data.conveyor_plane_quantile === 'number') setConvPlaneQuantile(res.data.conveyor_plane_quantile);
        if (typeof res.data.conveyor_plane_inlier_mm === 'number') setConvPlaneInlierMm(res.data.conveyor_plane_inlier_mm);
        if (typeof res.data.conveyor_object_min_height_mm === 'number') setConvObjectMinHeightMm(res.data.conveyor_object_min_height_mm);
        if (typeof res.data.conveyor_object_max_points === 'number') setConvObjectMaxPoints(res.data.conveyor_object_max_points);
      }
    } catch {
      // ignore
    }
  };

  const loadPreviewFilterSettings = async () => {
    try {
      const res = await api.get('/calibration/preview-filter-settings');
      const data = res.data || {};
      setPreviewEdgeFilterEnabled(!!data.use_edge_filter);
      if (typeof data.edge_curvature_threshold === 'number') {
        setPreviewEdgeCurvatureThreshold(Number(data.edge_curvature_threshold));
      }
      setPreviewVoxelDenoiseEnabled(!!data.use_voxel_denoise);
      if (typeof data.voxel_cell_mm === 'number') {
        setPreviewVoxelCellMm(Number(data.voxel_cell_mm));
      }
      if (typeof data.voxel_min_points_per_cell === 'number') {
        setPreviewVoxelMinPointsPerCell(Number(data.voxel_min_points_per_cell));
      }
      setPreviewVoxelKeepLargestComponent(!!data.voxel_keep_largest_component);
      setPreviewRegionEnabled(!!data.use_region_filter);
      if (Array.isArray(data.region_rect_norm) && data.region_rect_norm.length === 4) {
        const [x0, y0, x1, y1] = data.region_rect_norm;
        setPreviewRegionRect({
          x0: Number(x0),
          y0: Number(y0),
          x1: Number(x1),
          y1: Number(y1),
        });
      }
      setPreviewOrthogonalFilterEnabled(!!data.use_orthogonal_filter);
      if (typeof data.orthogonal_angle_tolerance_deg === 'number') {
        setPreviewOrthogonalToleranceDeg(Number(data.orthogonal_angle_tolerance_deg));
      }
      setPreviewNoiseFilterEnabled(!!data.use_noise_filter);
      if (typeof data.noise_filter_k === 'number') {
        setPreviewNoiseFilterK(Number(data.noise_filter_k));
      }
      if (typeof data.noise_filter_std_ratio === 'number') {
        setPreviewNoiseFilterStdRatio(Number(data.noise_filter_std_ratio));
      }
      if (Array.isArray(data.visible_device_ids)) {
        setPreviewVisibleIds(data.visible_device_ids.map((v: unknown) => String(v)));
      }
    } catch {
      // ignore
    }
  };

  const loadAcquisitionLiveStatus = async () => {
    try {
      const res = await api.get('/acquisition/trigger/status');
      setAcquisitionLiveStatus(res.data || null);
    } catch {
      setAcquisitionLiveStatus(null);
    }
  };

  const loadIoState = async () => {
    try {
      const res = await api.get('/tdc/io-state');
      setIoState(res.data || null);
    } catch {
      setIoState(null);
    }
  };

  async function handleSaveFrameSettings(overrides?: Partial<{ width_m: number; height_m: number; origin_mode: typeof originMode; clip_points_to_frame: boolean }>) {
    setFrameSaving(true);
    setFrameSaveError(null);
    try {
      const width = overrides?.width_m ?? frameWidth;
      const height = overrides?.height_m ?? frameHeight;
      const origin = overrides?.origin_mode ?? originMode;
      const clip = overrides?.clip_points_to_frame ?? clipPointsToFrame;
      const safeWidth = Number.isFinite(width) && width > 0 ? width : 2.0;
      const safeHeight = Number.isFinite(height) && height > 0 ? height : 1.2;
      await api.put('/calibration/frame-settings', {
        width_m: safeWidth,
        height_m: safeHeight,
        origin_mode: origin,
        clip_points_to_frame: clip,
      });
      if (safeWidth !== frameWidth) setFrameWidth(safeWidth);
      if (safeHeight !== frameHeight) setFrameHeight(safeHeight);
      if (clip !== clipPointsToFrame) setClipPointsToFrame(clip);
      setFrameDirty(false);
      setFrameSavedAt(Date.now());
      setPreviewRefreshTick((v) => v + 1);
    } catch {
      setFrameSaveError('Failed to save frame settings');
    } finally {
      setFrameSaving(false);
    }
  }

  const handleToggleClipPoints = (nextChecked: boolean) => {
    setClipPointsToFrame(nextChecked);
    setFrameDirty(false);
    void handleSaveFrameSettings({ clip_points_to_frame: nextChecked });
  };

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

  async function handleSaveAnalysisSettings() {
    setAnalysisSaving(true);
    try {
      await api.put('/calibration/analysis-settings', {
        active_app: analysisApp,
        log_window_profiles: logWindowProfiles,
        log_min_points: logMinPoints,
        conveyor_plane_quantile: convPlaneQuantile,
        conveyor_plane_inlier_mm: convPlaneInlierMm,
        conveyor_object_min_height_mm: convObjectMinHeightMm,
        conveyor_object_max_points: convObjectMaxPoints,
      });
      setAnalysisSavedAt(Date.now());
    } finally {
      setAnalysisSaving(false);
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

  async function handleSavePreviewFilterSettings() {
    setPreviewFilterSaving(true);
    try {
      await api.put('/calibration/preview-filter-settings', {
        use_edge_filter: previewEdgeFilterEnabled,
        edge_curvature_threshold: previewEdgeCurvatureThreshold,
        use_voxel_denoise: previewVoxelDenoiseEnabled,
        voxel_cell_mm: previewVoxelCellMm,
        voxel_min_points_per_cell: previewVoxelMinPointsPerCell,
        voxel_keep_largest_component: previewVoxelKeepLargestComponent,
        use_region_filter: previewRegionEnabled,
        region_rect_norm: [normalizedRegionRect.x0, normalizedRegionRect.y0, normalizedRegionRect.x1, normalizedRegionRect.y1],
        use_orthogonal_filter: previewOrthogonalFilterEnabled,
        orthogonal_angle_tolerance_deg: previewOrthogonalToleranceDeg,
        use_noise_filter: previewNoiseFilterEnabled,
        noise_filter_k: previewNoiseFilterK,
        noise_filter_std_ratio: previewNoiseFilterStdRatio,
        visible_device_ids: previewVisibleIds,
      });
      setPreviewFilterSavedAt(Date.now());
    } finally {
      setPreviewFilterSaving(false);
    }
  }

  useEffect(() => {
    loadDevices();
    loadFrameSettings();
    loadMotionSettings();
    loadAnalysisSettings();
    loadTdcSettings();
    loadPreviewFilterSettings();
    loadTdcStatus();
    loadAcquisitionLiveStatus();
    loadIoState();
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
      loadAcquisitionLiveStatus();
      loadIoState();
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  const liveEncoderSpeedMps = acquisitionLiveStatus?.encoder_speed_mps ?? acquisitionLiveStatus?.speed_mps ?? null;

  useEffect(() => {
    if (!frameDirty) return;
    const timer = setTimeout(() => {
      void handleSaveFrameSettings();
    }, 700);
    return () => clearTimeout(timer);
  }, [frameWidth, frameHeight, originMode, clipPointsToFrame, frameDirty]);

  useEffect(() => {
    if (devices.length === 0) return;
    if (selectedIds.length > 0) return;
    const enabled = devices.filter((d) => d.enabled).map((d) => d.device_id);
    const initial = enabled.length > 0 ? enabled : [devices[0].device_id];
    setSelectedIds(initial);
  }, [devices, selectedIds]);

  useEffect(() => {
    if (selectedIds.length === 0) {
      setReferenceDeviceId('');
      return;
    }
    if (!referenceDeviceId || !selectedIds.includes(referenceDeviceId)) {
      setReferenceDeviceId(selectedIds[0]);
    }
  }, [selectedIds, referenceDeviceId]);

  useEffect(() => {
    if (!previewActive || previewVisibleIds.length === 0) return;
    previewFitDoneRef.current = false;
    let cancelled = false;
    const pollMs = 900;

    const fetchPreview = async (): Promise<void> => {
      if (cancelled || previewRequestInFlightRef.current) return;
      previewRequestInFlightRef.current = true;
      try {
        const res = await api.post('/calibration/preview', {
          device_ids: previewVisibleIds,
          max_points: 20000,
          calibration_overrides:
            previewAutoCalibrationEnabled && Object.keys(autoCalibCandidateMap).length > 0
              ? autoCalibCandidateMap
              : null,
          use_edge_filter: previewEdgeFilterEnabled,
          edge_curvature_threshold: previewEdgeCurvatureThreshold,
          use_voxel_denoise: previewVoxelDenoiseEnabled,
          voxel_cell_mm: previewVoxelCellMm,
          voxel_min_points_per_cell: previewVoxelMinPointsPerCell,
          voxel_keep_largest_component: previewVoxelKeepLargestComponent,
          use_region_filter: previewRegionEnabled,
          region_min_x_mm: previewRegionEnabled ? previewRegionWorldBounds.minX : null,
          region_max_x_mm: previewRegionEnabled ? previewRegionWorldBounds.maxX : null,
          region_min_z_mm: previewRegionEnabled ? previewRegionWorldBounds.minZ : null,
          region_max_z_mm: previewRegionEnabled ? previewRegionWorldBounds.maxZ : null,
          use_orthogonal_filter: previewOrthogonalFilterEnabled,
          orthogonal_angle_tolerance_deg: previewOrthogonalToleranceDeg,
          use_noise_filter: previewNoiseFilterEnabled,
          noise_filter_k: previewNoiseFilterK,
          noise_filter_std_ratio: previewNoiseFilterStdRatio,
        });
        setPreviewPoints(res.data?.points || []);
      } catch {
        setPreviewPoints([]);
      } finally {
        previewRequestInFlightRef.current = false;
        if (!cancelled) {
          previewPollTimerRef.current = setTimeout(() => {
            void fetchPreview();
          }, pollMs);
        }
      }
    };

    void fetchPreview();
    return () => {
      cancelled = true;
      if (previewPollTimerRef.current) {
        clearTimeout(previewPollTimerRef.current);
        previewPollTimerRef.current = null;
      }
      previewRequestInFlightRef.current = false;
    };
  }, [
    previewActive,
    previewVisibleIds,
    previewRefreshTick,
    previewAutoCalibrationEnabled,
    autoCalibCandidateMap,
    previewEdgeFilterEnabled,
    previewEdgeCurvatureThreshold,
    previewVoxelDenoiseEnabled,
    previewVoxelCellMm,
    previewVoxelMinPointsPerCell,
    previewVoxelKeepLargestComponent,
    previewRegionEnabled,
    previewRegionWorldBounds,
    previewOrthogonalFilterEnabled,
    previewOrthogonalToleranceDeg,
    previewNoiseFilterEnabled,
    previewNoiseFilterK,
    previewNoiseFilterStdRatio,
  ]);

  useEffect(() => {
    if (!previewActive) return;
    const selectedDevices = devices.filter((d) => previewVisibleIds.includes(d.device_id));
    if (selectedDevices.length === 0) return;
    setLiveAdjustments((prev) => {
      const next = { ...prev };
      for (const d of selectedDevices) {
        next[d.device_id] = getLiveAdjustmentFromDevice(d);
      }
      return next;
    });
    if (!previewDeviceId || !previewVisibleIds.includes(previewDeviceId)) {
      setPreviewDeviceId(selectedDevices[0].device_id);
    }
  }, [previewActive, previewVisibleIds, devices, previewDeviceId]);

  useEffect(() => {
    if (!previewActive) return;
    if (previewVisibleIds.length > 0) return;
    if (selectedIds.length > 0) {
      setPreviewVisibleIds(selectedIds);
      return;
    }
    const enabled = devices.filter((d) => d.enabled).map((d) => d.device_id);
    if (enabled.length > 0) {
      setPreviewVisibleIds(enabled);
    }
  }, [previewActive, previewVisibleIds, selectedIds, devices]);

  useEffect(() => {
    return () => {
      const timers = liveAdjustSaveTimersRef.current;
      Object.values(timers).forEach((t) => clearTimeout(t));
    };
  }, []);

  useEffect(() => {
    if (!previewActive) {
      previewFitDoneRef.current = false;
      return;
    }
    if (previewPoints.length === 0) return;
    if (previewFitDoneRef.current) return;
    previewFitDoneRef.current = true;
    setTimeout(() => {
      previewRef.current?.resetView();
      previewRef.current?.fitToPoints();
    }, 100);
  }, [previewActive, previewPoints]);

  // Disable auto reset/fit for live preview (manual control only)

  const handleOpenModal = (device?: Device) => {
    if (device) {
      setEditingDevice(device);
      setFormData({
        ...device,
        device_type: (device.device_type === 'lms4000' ? 'lms4000' : 'picoscan'),
        protocol: device.protocol || (device.device_type === 'lms4000' ? 'tcp' : 'udp'),
        format_type: device.format_type || (device.device_type === 'lms4000' ? 'lmdscandata' : 'compact'),
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
        device_type: 'picoscan',
        protocol: 'udp',
        format_type: 'compact',
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
    setIsModalOpen(true);
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setEditingDevice(null);
  };

  const handleSaveDevice = async () => {
    try {
      const payload = {
        ...formData,
        calibration: {
          ...(formData.calibration || { scale: 1.0, translation: [0, 0, 0], rotation_deg: [0, 0, 0] }),
          ...buildCalibrationPayloadFromFrame(formData.frame_position, formData.frame_rotation_deg),
        },
      };
      if (editingDevice) {
        const { device_id, ...updateData } = payload;
        await api.put(`/devices/${device_id}`, updateData);
      } else {
        await api.post('/devices/', payload);
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

  const runAutoCalibration = async (options?: { referenceDeviceId?: string; saveResult?: boolean }) => {
    if (selectedIds.length === 0) return;
    const resolvedReference = options?.referenceDeviceId ?? referenceDeviceId;
    const saveResult = options?.saveResult ?? autoCalibSaveResult;
    const ref = resolvedReference && selectedIds.includes(resolvedReference)
      ? resolvedReference
      : selectedIds[0];
    setLoading(true);
    try {
      const res = await api.post('/calibration/auto', {
        device_ids: selectedIds,
        reference_device_id: ref,
        method: 'icp',
        max_iterations: 50,
        save_result: saveResult,
      });
      const data = res.data as AutoCalibrationResponsePayload;
      setAutoResult(data);
      const nextCandidateMap: Record<string, { translation: number[]; rotation_deg: number[]; scale: number }> = {};
      (data.results || []).forEach((r) => {
        nextCandidateMap[r.device_id] = {
          translation: r.translation || [0, 0, 0],
          rotation_deg: r.rotation_deg || [0, 0, 0],
          scale: typeof r.scale === 'number' ? r.scale : 1.0,
        };
      });
      setAutoCalibCandidateMap(nextCandidateMap);
      if (!saveResult) {
        setPreviewAutoCalibrationEnabled(true);
      }
      if (saveResult) {
        await loadDevices();
      }
      setPreviewRefreshTick((v) => v + 1);
    } catch (error) {
      console.error('Auto-calibration failed:', error);
    } finally {
      setLoading(false);
    }
  };

  const applyAutoCalibrationCandidate = async () => {
    if (!autoResult || !autoResult.results || autoResult.results.length === 0) return;
    setAutoCalibApplying(true);
    try {
      await api.post('/calibration/apply-auto-results', {
        results: autoResult.results,
      });
      setAutoResult((prev) => (prev ? { ...prev, saved: true } : prev));
      await loadDevices();
      setPreviewRefreshTick((v) => v + 1);
    } catch (error) {
      console.error('Failed to apply auto-calibration results:', error);
    } finally {
      setAutoCalibApplying(false);
    }
  };

  const startRegionDrag = (
    mode: 'move' | 'nw' | 'ne' | 'sw' | 'se',
    event: React.MouseEvent<HTMLDivElement | HTMLButtonElement>
  ) => {
    if (!previewRegionEnabled) return;
    const host = previewViewportRef.current;
    if (!host) return;
    const rect = host.getBoundingClientRect();
    const startX = (event.clientX - rect.left) / Math.max(rect.width, 1);
    const startY = (event.clientY - rect.top) / Math.max(rect.height, 1);
    regionDragRef.current = { mode, startX, startY, startRect: normalizedRegionRect };
    event.preventDefault();
    event.stopPropagation();

    const minW = 0.04;
    const minH = 0.04;

    const onMove = (ev: MouseEvent) => {
      const state = regionDragRef.current;
      const hostMove = previewViewportRef.current;
      if (!state || !hostMove) return;
      const r = hostMove.getBoundingClientRect();
      const nx = (ev.clientX - r.left) / Math.max(r.width, 1);
      const ny = (ev.clientY - r.top) / Math.max(r.height, 1);
      const dx = nx - state.startX;
      const dy = ny - state.startY;
      const s = state.startRect;

      if (state.mode === 'move') {
        const w = s.x1 - s.x0;
        const h = s.y1 - s.y0;
        const x0 = Math.max(0, Math.min(1 - w, s.x0 + dx));
        const y0 = Math.max(0, Math.min(1 - h, s.y0 + dy));
        setPreviewRegionRect({ x0, y0, x1: x0 + w, y1: y0 + h });
        return;
      }

      let x0 = s.x0;
      let y0 = s.y0;
      let x1 = s.x1;
      let y1 = s.y1;
      if (state.mode === 'nw') {
        x0 = Math.max(0, Math.min(s.x1 - minW, s.x0 + dx));
        y0 = Math.max(0, Math.min(s.y1 - minH, s.y0 + dy));
      } else if (state.mode === 'ne') {
        x1 = Math.min(1, Math.max(s.x0 + minW, s.x1 + dx));
        y0 = Math.max(0, Math.min(s.y1 - minH, s.y0 + dy));
      } else if (state.mode === 'sw') {
        x0 = Math.max(0, Math.min(s.x1 - minW, s.x0 + dx));
        y1 = Math.min(1, Math.max(s.y0 + minH, s.y1 + dy));
      } else if (state.mode === 'se') {
        x1 = Math.min(1, Math.max(s.x0 + minW, s.x1 + dx));
        y1 = Math.min(1, Math.max(s.y0 + minH, s.y1 + dy));
      }
      setPreviewRegionRect({ x0, y0, x1, y1 });
    };

    const onUp = () => {
      regionDragRef.current = null;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
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
            <div className="mt-3 flex items-center gap-2">
              <label htmlFor="clip_points_to_frame" className="synergy-toggle">
                <input
                  id="clip_points_to_frame"
                  className="synergy-toggle-input"
                  type="checkbox"
                  checked={clipPointsToFrame}
                  onChange={(e) => handleToggleClipPoints(e.target.checked)}
                />
                <span className={`synergy-toggle-track ${clipPointsToFrame ? 'is-on' : ''}`} aria-hidden="true">
                  <span className="synergy-toggle-thumb" />
                </span>
                <span className="synergy-toggle-label text-xs text-gray-600">
                  Auto-filter points outside frame (X/Z)
                </span>
                <span className={`synergy-toggle-state ${clipPointsToFrame ? 'is-on' : ''}`}>
                  {clipPointsToFrame ? 'ON' : 'OFF'}
                </span>
              </label>
            </div>
            {frameSaveError && (
              <div className="mt-2 text-xs text-red-600">{frameSaveError}</div>
            )}
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
                        const cornerPos = getCornerPosition(corner, originMode);
                        const savedPos = Array.isArray(device.frame_position) && device.frame_position.length >= 2
                          ? device.frame_position
                          : null;
                        const isAtCorner = savedPos
                          ? (Math.abs(savedPos[0] - cornerPos[0]) < 1e-6 && Math.abs(savedPos[1] - cornerPos[1]) < 1e-6)
                          : true;
                        const calRot = device.calibration?.rotation_deg || null;
                        const pos = savedPos && !isAtCorner
                          ? savedPos
                          : cornerPos;
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
                    <label className="text-xs text-gray-600">Fallback encoder speed input (rps)</label>
                    <input
                      className="input mt-1"
                      type="number"
                      step="0.01"
                      value={encoderRps}
                      onChange={(e) => setEncoderRps(parseFloat(e.target.value))}
                    />
                  </div>
                  <div className="flex items-end text-xs text-gray-500">
                    Live speed (from encoder): {liveEncoderSpeedMps !== null
                      ? `${liveEncoderSpeedMps.toFixed(3)} m/s`
                      : 'n/a'}
                    {acquisitionLiveStatus?.encoder_rpm !== null && acquisitionLiveStatus?.encoder_rpm !== undefined
                      ? `, ${acquisitionLiveStatus.encoder_rpm.toFixed(1)} rpm`
                      : ''}
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
                <h2 className="text-xl font-semibold text-slate-900">Analysis App</h2>
                <p className="text-xs text-gray-500 mt-1">Select active post-acquisition analysis pipeline</p>
              </div>
              <button className="btn-secondary" onClick={handleSaveAnalysisSettings} disabled={analysisSaving}>
                {analysisSaving ? 'Saving...' : 'Save Analysis Settings'}
              </button>
            </div>
            <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="text-xs text-gray-600">Active application</label>
                <select className="input mt-1" value={analysisApp} onChange={(e) => setAnalysisApp(e.target.value as AnalysisApp)}>
                  <option value="log">Log measurement</option>
                  <option value="conveyor_object">Conveyor object measurement</option>
                </select>
              </div>
              {analysisApp === 'log' ? (
                <>
                  <div>
                    <label className="text-xs text-gray-600">Window profiles</label>
                    <input className="input mt-1" type="number" min={1} value={logWindowProfiles} onChange={(e) => setLogWindowProfiles(parseInt(e.target.value || '1'))} />
                  </div>
                  <div>
                    <label className="text-xs text-gray-600">Min points</label>
                    <input className="input mt-1" type="number" min={10} value={logMinPoints} onChange={(e) => setLogMinPoints(parseInt(e.target.value || '10'))} />
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <label className="text-xs text-gray-600">Plane seed quantile</label>
                    <input className="input mt-1" type="number" step="0.01" min={0.05} max={0.8} value={convPlaneQuantile} onChange={(e) => setConvPlaneQuantile(parseFloat(e.target.value))} />
                  </div>
                  <div>
                    <label className="text-xs text-gray-600">Plane inlier threshold (mm)</label>
                    <input className="input mt-1" type="number" step="0.5" min={1} value={convPlaneInlierMm} onChange={(e) => setConvPlaneInlierMm(parseFloat(e.target.value))} />
                  </div>
                  <div>
                    <label className="text-xs text-gray-600">Object min height over plane (mm)</label>
                    <input className="input mt-1" type="number" step="0.5" min={1} value={convObjectMinHeightMm} onChange={(e) => setConvObjectMinHeightMm(parseFloat(e.target.value))} />
                  </div>
                  <div>
                    <label className="text-xs text-gray-600">Max object points</label>
                    <input className="input mt-1" type="number" step="1000" min={1000} value={convObjectMaxPoints} onChange={(e) => setConvObjectMaxPoints(parseInt(e.target.value || '1000'))} />
                  </div>
                </>
              )}
            </div>
            {analysisSavedAt && (
              <div className="mt-2 text-xs text-gray-500 text-right">
                Saved {new Date(analysisSavedAt).toLocaleTimeString()}
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
                  onChange={(e) => setTdcTriggerInput(e.target.value as TriggerInputName)}
                >
                  {TRIGGER_INPUT_OPTIONS.map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
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
                  <div>encoder speed: {liveEncoderSpeedMps !== null ? `${liveEncoderSpeedMps.toFixed(3)} m/s` : '-'}</div>
                  <div>encoder rpm: {acquisitionLiveStatus?.encoder_rpm !== null && acquisitionLiveStatus?.encoder_rpm !== undefined ? acquisitionLiveStatus.encoder_rpm.toFixed(1) : '-'}</div>
                </div>
              </div>
            )}
            {ioState && (
              <div className="mt-3 rounded-lg border border-gray-200 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-900/40 p-3 text-xs text-gray-600">
                <div className="flex items-center justify-between">
                  <div className="font-semibold text-gray-700 dark:text-gray-300">IO State</div>
                  <div className="text-[11px] text-gray-500">live</div>
                </div>
                <div className="mt-2 pin-grid">
                  {Object.entries(ioState.states || {}).map(([name, entry]) => (
                    <div key={name} className="pin-card">
                      <div className="min-w-0">
                        <div className="font-mono text-[11px] font-semibold truncate">{name}</div>
                        <div className="text-[10px] text-gray-500 truncate">{entry?.label || 'UNKNOWN'}</div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span
                          className={`pin-chip ${
                            entry?.state === 2 ? 'is-high' : entry?.state === 1 ? 'is-low' : ''
                          }`}
                        >
                          {entry?.state === 2 ? 'HIGH' : entry?.state === 1 ? 'LOW' : 'UNK'}
                        </span>
                        <span
                          className={`pin-led ${
                            entry?.state === 2 ? 'is-high' : entry?.state === 1 ? 'is-low' : 'is-unknown'
                          }`}
                          title={entry?.label || 'UNKNOWN'}
                        />
                      </div>
                    </div>
                  ))}
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
                            <button className="btn-secondary btn-sm btn-icon" onClick={() => handleOpenModal(device)} aria-label="Edit">
                              <Edit size={14} />
                            </button>
                            <button className="btn-danger btn-sm btn-icon" onClick={() => handleDeleteDevice(device.device_id)} aria-label="Delete">
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
                <div className="min-w-[220px]">
                  <label className="text-xs text-gray-600">Reference device</label>
                  <select
                    className="input mt-1"
                    value={referenceDeviceId}
                    onChange={(e) => setReferenceDeviceId(e.target.value)}
                  >
                    {devices
                      .filter((d) => selectedIds.includes(d.device_id))
                      .map((d) => (
                        <option key={d.device_id} value={d.device_id}>
                          {d.name || d.device_id}
                        </option>
                      ))}
                  </select>
                </div>
                <button className="btn-primary" onClick={runAutoCalibration} disabled={loading || selectedIds.length === 0}>
                  <Wand2 size={14} />
                  Auto-Calibration (ICP)
                </button>
                <button
                  className="btn-secondary"
                  onClick={() => setPreviewActive((v) => !v)}
                  disabled={selectedIds.length === 0}
                >
                  {previewActive ? 'Stop Preview' : 'Live Preview'}
                </button>
              </div>
              {autoResult && (
                <div className="mt-3 text-xs text-gray-600 bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-800 rounded-lg p-3">
                  <div className="font-semibold text-gray-700 dark:text-gray-300 mb-1">Auto-calibration result</div>
                  <div className="text-[11px] text-gray-500 mb-3">
                    Mode: {autoResult.saved === false ? 'Preview only (not saved)' : 'Saved to device calibration'}
                  </div>
                  <div className="grid grid-cols-1 gap-2">
                    {(autoResult.results || []).map((r: AutoCalibrationResultItem) => {
                      const framePos = toFramePositionFromCalibration(r.translation);
                      return (
                        <div key={r.device_id} className="bg-white/70 dark:bg-gray-900/60 rounded-md px-3 py-2 border border-gray-200 dark:border-gray-800">
                          <div className="flex items-center justify-between">
                            <div className="text-xs font-semibold text-gray-700 dark:text-gray-200">{r.device_id}</div>
                            <div className="text-xs text-gray-500">score: {typeof r.score === 'number' ? r.score.toFixed(3) : '-'}</div>
                          </div>
                          <div className="mt-1 text-xs text-gray-500">
                            t[mm]: {r.translation?.map((v: number) => v.toFixed(2)).join(', ') || '-'}
                          </div>
                          <div className="text-xs text-gray-500">
                            r[deg]: {r.rotation_deg?.map((v: number) => v.toFixed(3)).join(', ') || '-'}
                          </div>
                          <div className="text-xs text-gray-500">
                            frame[m]: x={framePos.x.toFixed(4)}, y={framePos.y.toFixed(4)}, z={framePos.z.toFixed(4)} | yaw={Number(r.rotation_deg?.[1] ?? 0).toFixed(3)} deg
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {previewActive && (
          <div className="fixed inset-0 z-40 flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/45 backdrop-blur-sm" onClick={() => setPreviewActive(false)} />
            <div className="relative w-[min(1860px,99vw)] h-[min(1020px,97vh)] glass-card p-0 overflow-hidden">
              <div className="card-header flex items-center justify-between">
                <div className="text-lg font-semibold text-slate-900">Live Preview (Unified Frame)</div>
                <div className="flex items-center gap-3">
                  <div className="text-xs text-gray-500">{previewPoints.length} points</div>
                  <button className="btn-secondary btn-sm btn-icon" onClick={() => setPreviewActive(false)} aria-label="Close preview">
                    <X size={16} />
                  </button>
                </div>
              </div>
              <div className="card-body grid grid-cols-1 xl:grid-cols-[1fr_420px] gap-4 h-[calc(100%-64px)]">
                <div ref={previewViewportRef} className="relative h-full rounded-lg overflow-hidden border border-gray-200">
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
                    frameOriginMode={originMode}
                  />
                  {previewRegionEnabled && (
                    <div className="absolute inset-0 z-20 pointer-events-none">
                      <div
                        className="absolute border-2 border-cyan-400 bg-cyan-300/10 rounded-sm pointer-events-auto"
                        style={{
                          left: `${normalizedRegionRect.x0 * 100}%`,
                          top: `${normalizedRegionRect.y0 * 100}%`,
                          width: `${(normalizedRegionRect.x1 - normalizedRegionRect.x0) * 100}%`,
                          height: `${(normalizedRegionRect.y1 - normalizedRegionRect.y0) * 100}%`,
                          cursor: 'move',
                        }}
                        onMouseDown={(e) => startRegionDrag('move', e)}
                      >
                        <button
                          className="absolute -left-1.5 -top-1.5 w-3 h-3 rounded-full bg-cyan-400 border border-white"
                          style={{ cursor: 'nwse-resize' }}
                          onMouseDown={(e) => startRegionDrag('nw', e)}
                        />
                        <button
                          className="absolute -right-1.5 -top-1.5 w-3 h-3 rounded-full bg-cyan-400 border border-white"
                          style={{ cursor: 'nesw-resize' }}
                          onMouseDown={(e) => startRegionDrag('ne', e)}
                        />
                        <button
                          className="absolute -left-1.5 -bottom-1.5 w-3 h-3 rounded-full bg-cyan-400 border border-white"
                          style={{ cursor: 'nesw-resize' }}
                          onMouseDown={(e) => startRegionDrag('sw', e)}
                        />
                        <button
                          className="absolute -right-1.5 -bottom-1.5 w-3 h-3 rounded-full bg-cyan-400 border border-white"
                          style={{ cursor: 'nwse-resize' }}
                          onMouseDown={(e) => startRegionDrag('se', e)}
                        />
                      </div>
                    </div>
                  )}
                </div>
                <div className="h-full overflow-auto border border-gray-200 rounded-lg p-3 bg-white/70">
                  <div className="flex items-center justify-between">
                    <div className="text-sm font-semibold text-slate-800">Live Device Placement</div>
                    <div className="flex items-center gap-1">
                      <button className="btn-secondary btn-sm" onClick={loadPreviewFilterSettings} type="button">
                        Reload Config
                      </button>
                      <button className="btn-success btn-sm" onClick={handleSavePreviewFilterSettings} disabled={previewFilterSaving} type="button">
                        {previewFilterSaving ? 'Saving...' : 'Save Filter Config'}
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-gray-500 mt-1">
                    Zmiany zapisują się automatycznie i odświeżają podgląd na żywo.
                  </p>
                  {previewFilterSavedAt && (
                    <div className="mt-1 text-[11px] text-gray-500">
                      Filter config saved {new Date(previewFilterSavedAt).toLocaleTimeString()}
                    </div>
                  )}
                  <div className="mt-3 rounded-lg border border-gray-200 bg-white px-2.5 py-2">
                    <div className="flex items-center justify-between">
                      <label className="text-xs text-gray-600">Visible scanners (preview)</label>
                      <div className="flex items-center gap-1">
                        <button
                          className="btn-secondary btn-sm"
                          onClick={() => setPreviewVisibleIds(devices.filter((d) => d.enabled).map((d) => d.device_id))}
                          type="button"
                        >
                          All
                        </button>
                        <button
                          className="btn-secondary btn-sm"
                          onClick={() => setPreviewVisibleIds(selectedIds)}
                          type="button"
                        >
                          Selected
                        </button>
                      </div>
                    </div>
                    <select
                      className="input mt-1"
                      multiple
                      value={previewVisibleIds}
                      onChange={(e) => {
                        const opts = Array.from(e.target.selectedOptions).map((o) => o.value);
                        setPreviewVisibleIds(opts);
                      }}
                    >
                      {devices
                        .filter((d) => d.enabled)
                        .map((d) => (
                          <option key={`preview-visible-${d.device_id}`} value={d.device_id}>
                            {d.name || d.device_id}
                          </option>
                        ))}
                    </select>
                  </div>
                  <div className="mt-3 rounded-lg border border-gray-200 bg-white px-2.5 py-2 space-y-2.5">
                    <div className="text-xs font-semibold text-gray-700">Auto-calibration (ICP)</div>
                    <div>
                      <label className="text-xs text-gray-600">Reference device</label>
                      <select
                        className="input mt-1"
                        value={referenceDeviceId}
                        onChange={(e) => setReferenceDeviceId(e.target.value)}
                      >
                        {devices
                          .filter((d) => selectedIds.includes(d.device_id))
                          .map((d) => (
                            <option key={d.device_id} value={d.device_id}>
                              {d.name || d.device_id}
                            </option>
                          ))}
                      </select>
                    </div>
                    <label htmlFor="auto_calib_save_result_preview" className="synergy-toggle">
                      <input
                        id="auto_calib_save_result_preview"
                        className="synergy-toggle-input"
                        type="checkbox"
                        checked={autoCalibSaveResult}
                        onChange={(e) => setAutoCalibSaveResult(e.target.checked)}
                      />
                      <span className={`synergy-toggle-track ${autoCalibSaveResult ? 'is-on' : ''}`} aria-hidden="true">
                        <span className="synergy-toggle-thumb" />
                      </span>
                      <span className="synergy-toggle-label text-xs text-gray-700">
                        Save calibration after run
                      </span>
                      <span className={`synergy-toggle-state ${autoCalibSaveResult ? 'is-on' : ''}`}>
                        {autoCalibSaveResult ? 'ON' : 'OFF'}
                      </span>
                    </label>
                    <button
                      className="btn-primary w-full"
                      onClick={() => runAutoCalibration({ referenceDeviceId, saveResult: autoCalibSaveResult })}
                      disabled={loading || selectedIds.length === 0}
                    >
                      <Wand2 size={14} />
                      {loading ? 'Calibrating...' : 'Run Auto-Calibration'}
                    </button>
                    <label htmlFor="preview_auto_calibration_toggle" className="synergy-toggle">
                      <input
                        id="preview_auto_calibration_toggle"
                        className="synergy-toggle-input"
                        type="checkbox"
                        checked={previewAutoCalibrationEnabled}
                        onChange={(e) => setPreviewAutoCalibrationEnabled(e.target.checked)}
                        disabled={!autoResult || (autoResult.results || []).length === 0}
                      />
                      <span className={`synergy-toggle-track ${previewAutoCalibrationEnabled ? 'is-on' : ''}`} aria-hidden="true">
                        <span className="synergy-toggle-thumb" />
                      </span>
                      <span className="synergy-toggle-label text-xs text-gray-700">
                        Preview auto-calibration result
                      </span>
                      <span className={`synergy-toggle-state ${previewAutoCalibrationEnabled ? 'is-on' : ''}`}>
                        {previewAutoCalibrationEnabled ? 'ON' : 'OFF'}
                      </span>
                    </label>
                    <div className="text-[11px] text-gray-500">
                      Workflow: run ICP without save, compare in preview, then apply to config.
                    </div>
                    <button
                      className="btn-success w-full"
                      onClick={applyAutoCalibrationCandidate}
                      disabled={autoCalibApplying || !autoResult || (autoResult.results || []).length === 0 || autoResult.saved === true}
                    >
                      {autoCalibApplying ? 'Applying...' : 'Apply Auto-Calibration To Config'}
                    </button>
                    {autoResult && (autoResult.results || []).length > 0 && (
                      <div className="rounded border border-gray-200 bg-gray-50/70 px-2 py-2">
                        <div className="text-[11px] font-semibold text-gray-700 mb-1">Transform preview</div>
                        <div className="space-y-1">
                          {autoResult.results.map((r: AutoCalibrationResultItem) => {
                            const framePos = toFramePositionFromCalibration(r.translation);
                            return (
                              <div key={`preview-transform-${r.device_id}`} className="text-[11px] text-gray-600 leading-4">
                                <div className="font-medium text-gray-700">{r.device_id}</div>
                                <div>t[mm]: {r.translation?.map((v: number) => v.toFixed(2)).join(', ') || '-'}</div>
                                <div>r[deg]: {r.rotation_deg?.map((v: number) => v.toFixed(3)).join(', ') || '-'}</div>
                                <div>frame[m]: x={framePos.x.toFixed(4)} y={framePos.y.toFixed(4)} z={framePos.z.toFixed(4)} | yaw={Number(r.rotation_deg?.[1] ?? 0).toFixed(3)}</div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="mt-3 rounded-lg border border-gray-200 bg-white px-2.5 py-2">
                    <label htmlFor="clip_points_to_frame_preview" className="synergy-toggle">
                      <input
                        id="clip_points_to_frame_preview"
                        className="synergy-toggle-input"
                        type="checkbox"
                        checked={clipPointsToFrame}
                        onChange={(e) => handleToggleClipPoints(e.target.checked)}
                      />
                      <span className={`synergy-toggle-track ${clipPointsToFrame ? 'is-on' : ''}`} aria-hidden="true">
                        <span className="synergy-toggle-thumb" />
                      </span>
                      <span className="synergy-toggle-label text-xs text-gray-700">
                        Clip points to frame
                      </span>
                      <span className={`synergy-toggle-state ${clipPointsToFrame ? 'is-on' : ''}`}>
                        {clipPointsToFrame ? 'ON' : 'OFF'}
                      </span>
                    </label>
                    <div className="mt-2">
                      <label htmlFor="voxel_denoise_preview" className="synergy-toggle">
                        <input
                          id="voxel_denoise_preview"
                          className="synergy-toggle-input"
                          type="checkbox"
                          checked={previewVoxelDenoiseEnabled}
                          onChange={(e) => setPreviewVoxelDenoiseEnabled(e.target.checked)}
                        />
                        <span className={`synergy-toggle-track ${previewVoxelDenoiseEnabled ? 'is-on' : ''}`} aria-hidden="true">
                          <span className="synergy-toggle-thumb" />
                        </span>
                        <span className="synergy-toggle-label text-xs text-gray-700">
                          Shadow denoise (voxel)
                        </span>
                        <span className={`synergy-toggle-state ${previewVoxelDenoiseEnabled ? 'is-on' : ''}`}>
                          {previewVoxelDenoiseEnabled ? 'ON' : 'OFF'}
                        </span>
                      </label>
                    </div>
                    {previewVoxelDenoiseEnabled && (
                      <div className="mt-2 space-y-2 rounded border border-gray-200 bg-gray-50/60 px-2 py-2">
                        <div>
                          <label className="text-xs text-gray-600">Voxel cell size: {previewVoxelCellMm} mm</label>
                          <input
                            className="w-full mt-1"
                            type="range"
                            min={2}
                            max={40}
                            step={1}
                            value={previewVoxelCellMm}
                            onChange={(e) => setPreviewVoxelCellMm(Number(e.target.value))}
                          />
                        </div>
                        <div>
                          <label className="text-xs text-gray-600">Min points per voxel: {previewVoxelMinPointsPerCell}</label>
                          <input
                            className="w-full mt-1"
                            type="range"
                            min={1}
                            max={12}
                            step={1}
                            value={previewVoxelMinPointsPerCell}
                            onChange={(e) => setPreviewVoxelMinPointsPerCell(Number(e.target.value))}
                          />
                        </div>
                        <label htmlFor="voxel_keep_lcc_preview" className="synergy-toggle">
                          <input
                            id="voxel_keep_lcc_preview"
                            className="synergy-toggle-input"
                            type="checkbox"
                            checked={previewVoxelKeepLargestComponent}
                            onChange={(e) => setPreviewVoxelKeepLargestComponent(e.target.checked)}
                          />
                          <span className={`synergy-toggle-track ${previewVoxelKeepLargestComponent ? 'is-on' : ''}`} aria-hidden="true">
                            <span className="synergy-toggle-thumb" />
                          </span>
                          <span className="synergy-toggle-label text-xs text-gray-700">
                            Keep largest component
                          </span>
                          <span className={`synergy-toggle-state ${previewVoxelKeepLargestComponent ? 'is-on' : ''}`}>
                            {previewVoxelKeepLargestComponent ? 'ON' : 'OFF'}
                          </span>
                        </label>
                      </div>
                    )}
                    <div className="mt-2">
                      <label htmlFor="region_filter_preview" className="synergy-toggle">
                        <input
                          id="region_filter_preview"
                          className="synergy-toggle-input"
                          type="checkbox"
                          checked={previewRegionEnabled}
                          onChange={(e) => setPreviewRegionEnabled(e.target.checked)}
                        />
                        <span className={`synergy-toggle-track ${previewRegionEnabled ? 'is-on' : ''}`} aria-hidden="true">
                          <span className="synergy-toggle-thumb" />
                        </span>
                        <span className="synergy-toggle-label text-xs text-gray-700">
                          Region filter (ROI rectangle)
                        </span>
                        <span className={`synergy-toggle-state ${previewRegionEnabled ? 'is-on' : ''}`}>
                          {previewRegionEnabled ? 'ON' : 'OFF'}
                        </span>
                      </label>
                    </div>
                    {previewRegionEnabled && (
                      <div className="mt-2 space-y-2 rounded border border-gray-200 bg-gray-50/60 px-2 py-2">
                        <div className="text-[11px] text-gray-600">
                          Drag rectangle on preview. Resize by grabbing corners.
                        </div>
                        <div className="text-[11px] text-gray-600">
                          X: {previewRegionWorldBounds.minX.toFixed(1)} .. {previewRegionWorldBounds.maxX.toFixed(1)} mm
                        </div>
                        <div className="text-[11px] text-gray-600">
                          Z: {previewRegionWorldBounds.minZ.toFixed(1)} .. {previewRegionWorldBounds.maxZ.toFixed(1)} mm
                        </div>
                        <button
                          className="btn-secondary btn-sm"
                          onClick={() => setPreviewRegionRect({ x0: 0.2, y0: 0.15, x1: 0.8, y1: 0.85 })}
                        >
                          Reset Region
                        </button>
                      </div>
                    )}
                    <div className="mt-2">
                      <label htmlFor="orthogonal_filter_preview" className="synergy-toggle">
                        <input
                          id="orthogonal_filter_preview"
                          className="synergy-toggle-input"
                          type="checkbox"
                          checked={previewOrthogonalFilterEnabled}
                          onChange={(e) => setPreviewOrthogonalFilterEnabled(e.target.checked)}
                        />
                        <span className={`synergy-toggle-track ${previewOrthogonalFilterEnabled ? 'is-on' : ''}`} aria-hidden="true">
                          <span className="synergy-toggle-thumb" />
                        </span>
                        <span className="synergy-toggle-label text-xs text-gray-700">
                          Orthogonal surfaces only
                        </span>
                        <span className={`synergy-toggle-state ${previewOrthogonalFilterEnabled ? 'is-on' : ''}`}>
                          {previewOrthogonalFilterEnabled ? 'ON' : 'OFF'}
                        </span>
                      </label>
                    </div>
                    {previewOrthogonalFilterEnabled && (
                      <div className="mt-2 rounded border border-gray-200 bg-gray-50/60 px-2 py-2">
                        <label className="text-xs text-gray-600">
                          Angle tolerance: +/- {previewOrthogonalToleranceDeg} deg
                        </label>
                        <input
                          className="w-full mt-1"
                          type="range"
                          min={3}
                          max={35}
                          step={1}
                          value={previewOrthogonalToleranceDeg}
                          onChange={(e) => setPreviewOrthogonalToleranceDeg(Number(e.target.value))}
                        />
                      </div>
                    )}
                    <div className="mt-2">
                      <label htmlFor="noise_filter_preview" className="synergy-toggle">
                        <input
                          id="noise_filter_preview"
                          className="synergy-toggle-input"
                          type="checkbox"
                          checked={previewNoiseFilterEnabled}
                          onChange={(e) => setPreviewNoiseFilterEnabled(e.target.checked)}
                        />
                        <span className={`synergy-toggle-track ${previewNoiseFilterEnabled ? 'is-on' : ''}`} aria-hidden="true">
                          <span className="synergy-toggle-thumb" />
                        </span>
                        <span className="synergy-toggle-label text-xs text-gray-700">
                          Noise filter (statistical)
                        </span>
                        <span className={`synergy-toggle-state ${previewNoiseFilterEnabled ? 'is-on' : ''}`}>
                          {previewNoiseFilterEnabled ? 'ON' : 'OFF'}
                        </span>
                      </label>
                    </div>
                    {previewNoiseFilterEnabled && (
                      <div className="mt-2 space-y-2 rounded border border-gray-200 bg-gray-50/60 px-2 py-2">
                        <div>
                          <label className="text-xs text-gray-600">Neighbors (k): {previewNoiseFilterK}</label>
                          <input
                            className="w-full mt-1"
                            type="range"
                            min={4}
                            max={48}
                            step={1}
                            value={previewNoiseFilterK}
                            onChange={(e) => setPreviewNoiseFilterK(Number(e.target.value))}
                          />
                        </div>
                        <div>
                          <label className="text-xs text-gray-600">
                            Outlier sensitivity (std ratio): {previewNoiseFilterStdRatio.toFixed(2)}
                          </label>
                          <input
                            className="w-full mt-1"
                            type="range"
                            min={0.2}
                            max={3.0}
                            step={0.1}
                            value={previewNoiseFilterStdRatio}
                            onChange={(e) => setPreviewNoiseFilterStdRatio(Number(e.target.value))}
                          />
                        </div>
                      </div>
                    )}
                    <div className="mt-2">
                      <label htmlFor="edge_filter_preview" className="synergy-toggle">
                        <input
                          id="edge_filter_preview"
                          className="synergy-toggle-input"
                          type="checkbox"
                          checked={previewEdgeFilterEnabled}
                          onChange={(e) => setPreviewEdgeFilterEnabled(e.target.checked)}
                        />
                        <span className={`synergy-toggle-track ${previewEdgeFilterEnabled ? 'is-on' : ''}`} aria-hidden="true">
                          <span className="synergy-toggle-thumb" />
                        </span>
                        <span className="synergy-toggle-label text-xs text-gray-700">
                          Edge filter (preview)
                        </span>
                        <span className={`synergy-toggle-state ${previewEdgeFilterEnabled ? 'is-on' : ''}`}>
                          {previewEdgeFilterEnabled ? 'ON' : 'OFF'}
                        </span>
                      </label>
                    </div>
                    {previewEdgeFilterEnabled && (
                      <div className="mt-2 rounded border border-gray-200 bg-gray-50/60 px-2 py-2">
                        <label className="text-xs text-gray-600">
                          Edge curvature threshold: {previewEdgeCurvatureThreshold.toFixed(2)}
                        </label>
                        <input
                          className="w-full mt-1"
                          type="range"
                          min={0.01}
                          max={0.50}
                          step={0.01}
                          value={previewEdgeCurvatureThreshold}
                          onChange={(e) => setPreviewEdgeCurvatureThreshold(Number(e.target.value))}
                        />
                      </div>
                    )}
                  </div>
                  <div className="mt-3">
                    <label className="text-xs text-gray-600">Scanner</label>
                    <select
                      className="input mt-1"
                      value={previewDeviceId}
                      onChange={(e) => setPreviewDeviceId(e.target.value)}
                    >
                      {devices
                        .filter((d) => previewVisibleIds.includes(d.device_id))
                        .map((d) => (
                          <option key={d.device_id} value={d.device_id}>
                            {d.name || d.device_id}
                          </option>
                        ))}
                    </select>
                  </div>
                  {previewDeviceId && liveAdjustments[previewDeviceId] && (
                    <div className="mt-3 space-y-3">
                      <div>
                        <label className="text-xs text-gray-600">Position X [m]</label>
                        <input
                          className="input mt-1"
                          type="number"
                          step="0.01"
                          value={liveAdjustments[previewDeviceId].x}
                          onChange={(e) => handleLiveAdjustmentChange(previewDeviceId, { x: Number(e.target.value) })}
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-600">Position Y [m]</label>
                        <input
                          className="input mt-1"
                          type="number"
                          step="0.01"
                          value={liveAdjustments[previewDeviceId].y}
                          onChange={(e) => handleLiveAdjustmentChange(previewDeviceId, { y: Number(e.target.value) })}
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-600">Position Z [m]</label>
                        <input
                          className="input mt-1"
                          type="number"
                          step="0.01"
                          value={liveAdjustments[previewDeviceId].z}
                          onChange={(e) => handleLiveAdjustmentChange(previewDeviceId, { z: Number(e.target.value) })}
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-600">Rotation Z [deg]</label>
                        <input
                          className="input mt-1"
                          type="number"
                          step="0.5"
                          value={liveAdjustments[previewDeviceId].yaw}
                          onChange={(e) => handleLiveAdjustmentChange(previewDeviceId, { yaw: Number(e.target.value) })}
                        />
                      </div>
                      <div className="grid grid-cols-4 gap-2">
                        <button className="btn-secondary btn-sm" onClick={() => handleLiveAdjustmentChange(previewDeviceId, { x: liveAdjustments[previewDeviceId].x - 0.01 })}>X-</button>
                        <button className="btn-secondary btn-sm" onClick={() => handleLiveAdjustmentChange(previewDeviceId, { x: liveAdjustments[previewDeviceId].x + 0.01 })}>X+</button>
                        <button className="btn-secondary btn-sm" onClick={() => handleLiveAdjustmentChange(previewDeviceId, { y: liveAdjustments[previewDeviceId].y - 0.01 })}>Y-</button>
                        <button className="btn-secondary btn-sm" onClick={() => handleLiveAdjustmentChange(previewDeviceId, { y: liveAdjustments[previewDeviceId].y + 0.01 })}>Y+</button>
                        <button className="btn-secondary btn-sm" onClick={() => handleLiveAdjustmentChange(previewDeviceId, { z: liveAdjustments[previewDeviceId].z - 0.01 })}>Z-</button>
                        <button className="btn-secondary btn-sm" onClick={() => handleLiveAdjustmentChange(previewDeviceId, { z: liveAdjustments[previewDeviceId].z + 0.01 })}>Z+</button>
                        <button className="btn-secondary btn-sm" onClick={() => handleLiveAdjustmentChange(previewDeviceId, { yaw: liveAdjustments[previewDeviceId].yaw - 1 })}>R-</button>
                        <button className="btn-secondary btn-sm" onClick={() => handleLiveAdjustmentChange(previewDeviceId, { yaw: liveAdjustments[previewDeviceId].yaw + 1 })}>R+</button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
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
              <button className="btn-secondary btn-sm btn-icon" onClick={handleCloseModal}>
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
                <div>
                  <label className="text-xs text-gray-600">Device type</label>
                  <select
                    className="input mt-1"
                    value={formData.device_type}
                    onChange={(e) => {
                      const nextType = e.target.value as 'picoscan' | 'lms4000';
                      const nextFormat =
                        nextType === 'lms4000'
                          ? 'lmdscandata'
                          : (formData.format_type === 'msgpack' ? 'msgpack' : 'compact');
                      setFormData({
                        ...formData,
                        device_type: nextType,
                        protocol: nextType === 'lms4000' ? 'tcp' : 'udp',
                        format_type: nextFormat,
                        port: nextType === 'lms4000' ? 2111 : 2115,
                      });
                    }}
                  >
                    <option value="picoscan">Picoscan (UDP)</option>
                    <option value="lms4000">LMS4000 (TCP LMDscandata)</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-gray-600">Data format</label>
                  <select
                    className="input mt-1"
                    value={formData.format_type}
                    onChange={(e) => setFormData({ ...formData, format_type: e.target.value })}
                    disabled={formData.device_type === 'lms4000'}
                  >
                    {formData.device_type === 'lms4000' ? (
                      <option value="lmdscandata">lmdscandata</option>
                    ) : (
                      <>
                        <option value="compact">compact</option>
                        <option value="msgpack">messagepack</option>
                      </>
                    )}
                  </select>
                </div>
                <input
                  className="input"
                  placeholder={formData.device_type === 'lms4000' ? 'Sensor IP Address' : 'IP Address'}
                  value={formData.ip_address}
                  onChange={(e) => setFormData({ ...formData, ip_address: e.target.value })}
                />
                <input
                  className="input"
                  type="number"
                  placeholder="Port"
                  value={formData.port.toString()}
                  onChange={(e) => setFormData({ ...formData, port: parseInt(e.target.value) })}
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
                        setFormData({ ...formData, frame_position: [parseFloat(e.target.value), formData.frame_position[1], formData.frame_position[2]] });
                      }}
                    />
                    <input
                      className="input"
                      type="number"
                      value={formData.frame_position[1]}
                      onChange={(e) => {
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
                        setFormData({
                          ...formData,
                          frame_rotation_deg: [0, 0, parseFloat(e.target.value)],
                        });
                      }}
                    />
                    <div className="flex gap-2">
                      <button
                        className="btn-secondary btn-sm"
                        type="button"
                        onClick={() => {
                          const v = (formData.frame_rotation_deg[2] ?? 0) - 5;
                          setFormData({ ...formData, frame_rotation_deg: [0, 0, v] });
                        }}
                      >
                        -5°
                      </button>
                      <button
                        className="btn-secondary btn-sm"
                        type="button"
                        onClick={() => {
                          const v = (formData.frame_rotation_deg[2] ?? 0) - 1;
                          setFormData({ ...formData, frame_rotation_deg: [0, 0, v] });
                        }}
                      >
                        -1°
                      </button>
                      <button
                        className="btn-secondary btn-sm"
                        type="button"
                        onClick={() => {
                          const v = (formData.frame_rotation_deg[2] ?? 0) + 1;
                          setFormData({ ...formData, frame_rotation_deg: [0, 0, v] });
                        }}
                      >
                        +1°
                      </button>
                      <button
                        className="btn-secondary btn-sm"
                        type="button"
                        onClick={() => {
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
              <button className="btn-secondary btn-sm btn-icon" onClick={closeDetails}>
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
