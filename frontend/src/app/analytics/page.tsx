'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import Layout from '@/components/layout/Layout';
import api from '@/services/api';
import PointCloudThreeViewer, { type PointCloudThreeViewerHandle } from '@/components/visualization/PointCloudThreeViewer';
import { Play, Square, RefreshCw, Wand2 } from 'lucide-react';

interface TriggerStatus {
  recording: boolean;
  distance_mm: number;
  speed_mps: number | null;
  profiling_distance_mm: number | null;
  profiles_count?: number;
  points_count: number;
  last_update_ts: number | null;
}

interface LogSlice {
  position_mm: number;
  center_mm: [number, number];
  radius_mm: number;
  diameter_mm: number;
  area_mm2: number;
  circumference_mm: number;
  points_used: number;
}

interface LogMetrics {
  total_slices: number;
  total_length_mm: number;
  volume_mm3: number;
  volume_m3: number;
  diameter_mm: { min: number; max: number; avg: number };
  slices: LogSlice[];
}

type AnalysisApp = 'log' | 'conveyor_object';
type ConveyorLocalizationAlgorithm = 'object_cloud_bbox' | 'box_top_plane';

interface ConveyorMetrics {
  analysis_app: 'conveyor_object';
  plane?: {
    inliers_count?: number;
    rmse_mm?: number | null;
  };
  object?: {
    localization_algorithm?: ConveyorLocalizationAlgorithm;
    points_count?: number;
    centroid_mm?: [number, number, number];
    bbox_mm?: {
      length?: number;
      width?: number;
      height?: number;
    };
    bbox_volume_mm3?: number;
    bbox_volume_m3?: number;
    height_above_plane_mm?: {
      min?: number;
      max?: number;
      avg?: number;
    };
    top_plane?: {
      points_count?: number;
      height_avg_mm?: number;
      height_min_mm?: number;
      height_max_mm?: number;
      footprint_angle_deg?: number;
    } | null;
  };
}

function isLogMetrics(value: unknown): value is LogMetrics {
  if (!value || typeof value !== 'object') return false;
  const v = value as { slices?: unknown; total_slices?: unknown; diameter_mm?: unknown };
  return Array.isArray(v.slices) && typeof v.total_slices === 'number' && typeof v.diameter_mm === 'object';
}

function isConveyorMetrics(value: unknown): value is ConveyorMetrics {
  if (!value || typeof value !== 'object') return false;
  return (value as { analysis_app?: string }).analysis_app === 'conveyor_object';
}

function mapPointToViewer(point: number[]): number[] {
  if (!Array.isArray(point) || point.length < 3) return point;
  const x = Number(point[0]);
  const y = Number(point[1]);
  const z = Number(point[2]);
  const mapped: number[] = [z, y, x];
  if (point.length > 3) mapped.push(...point.slice(3));
  return mapped;
}

function buildAabbAnnotations(
  minX: number,
  maxX: number,
  minY: number,
  maxY: number,
  minZ: number,
  maxZ: number
): { start: [number, number, number]; end: [number, number, number]; color?: number; label?: string; noArrow?: boolean }[] {
  const p000: [number, number, number] = [minX, minY, minZ];
  const p001: [number, number, number] = [minX, minY, maxZ];
  const p010: [number, number, number] = [minX, maxY, minZ];
  const p011: [number, number, number] = [minX, maxY, maxZ];
  const p100: [number, number, number] = [maxX, minY, minZ];
  const p101: [number, number, number] = [maxX, minY, maxZ];
  const p110: [number, number, number] = [maxX, maxY, minZ];
  const p111: [number, number, number] = [maxX, maxY, maxZ];

  const edges: Array<[[number, number, number], [number, number, number]]> = [
    [p000, p001], [p000, p010], [p000, p100],
    [p111, p110], [p111, p101], [p111, p011],
    [p001, p011], [p001, p101],
    [p010, p011], [p010, p110],
    [p100, p101], [p100, p110],
  ];

  return edges.map((e) => ({
    start: e[0],
    end: e[1],
    color: 0x22c55e,
    noArrow: true,
  }));
}

function solve3x3(a: number[][], b: number[]): number[] | null {
  const m = a.map((row, i) => [...row, b[i]]);
  const n = 3;
  for (let col = 0; col < n; col += 1) {
    let pivot = col;
    for (let r = col + 1; r < n; r += 1) {
      if (Math.abs(m[r][col]) > Math.abs(m[pivot][col])) pivot = r;
    }
    if (Math.abs(m[pivot][col]) < 1e-12) return null;
    if (pivot !== col) {
      const tmp = m[pivot];
      m[pivot] = m[col];
      m[col] = tmp;
    }
    const div = m[col][col];
    for (let c = col; c <= n; c += 1) m[col][c] /= div;
    for (let r = 0; r < n; r += 1) {
      if (r === col) continue;
      const factor = m[r][col];
      for (let c = col; c <= n; c += 1) {
        m[r][c] -= factor * m[col][c];
      }
    }
  }
  return [m[0][3], m[1][3], m[2][3]];
}

function fitCircleKasa(points: number[][]): { cx: number; cy: number; r: number } | null {
  if (points.length < 3) return null;
  let sx = 0;
  let sy = 0;
  let sxx = 0;
  let syy = 0;
  let sxy = 0;
  let sxxx = 0;
  let syyy = 0;
  let sxxy = 0;
  let sxyy = 0;
  for (const p of points) {
    const x = p[0];
    const y = p[1];
    const xx = x * x;
    const yy = y * y;
    sx += x;
    sy += y;
    sxx += xx;
    syy += yy;
    sxy += x * y;
    sxxx += xx * x;
    syyy += yy * y;
    sxxy += xx * y;
    sxyy += x * yy;
  }
  const n = points.length;
  const a = [
    [2 * sxx, 2 * sxy, 2 * sx],
    [2 * sxy, 2 * syy, 2 * sy],
    [2 * sx, 2 * sy, 2 * n],
  ];
  const b = [sxxx + sxyy, sxxy + syyy, sxx + syy];
  const c = solve3x3(a, b);
  if (!c) return null;
  const cx = c[0];
  const cy = c[1];
  const rSq = c[2] + cx * cx + cy * cy;
  if (!Number.isFinite(rSq) || rSq <= 0) return null;
  return { cx, cy, r: Math.sqrt(rSq) };
}

function computeLogMetrics(
  points: number[][],
  profilingDistanceMm: number,
  windowProfiles: number,
  minPoints: number,
  yMin?: number,
  yMax?: number
): LogMetrics | null {
  if (!points.length || profilingDistanceMm <= 0) return null;
  const profileIndex = points.map((p) => Math.round(p[1] / profilingDistanceMm));
  const uniqueProfiles = Array.from(new Set(profileIndex)).sort((a, b) => a - b);
  const slices: LogSlice[] = [];
  for (let start = 0; start < uniqueProfiles.length; start += windowProfiles) {
    const window = uniqueProfiles.slice(start, start + windowProfiles);
    if (window.length < windowProfiles) break;
    const windowSet = new Set(window);
    const windowPts = points.filter((_, idx) => windowSet.has(profileIndex[idx]));
    if (windowPts.length < minPoints) continue;
    const xz = windowPts.map((p) => [p[0], p[2]]);
    const fit = fitCircleKasa(xz);
    if (!fit) continue;
    const diameter = 2 * fit.r;
    const area = Math.PI * fit.r * fit.r;
    const circumference = 2 * Math.PI * fit.r;
    const pos = (window.reduce((acc, v) => acc + v, 0) / window.length) * profilingDistanceMm;
    slices.push({
      position_mm: pos,
      center_mm: [fit.cx, fit.cy],
      radius_mm: fit.r,
      diameter_mm: diameter,
      area_mm2: area,
      circumference_mm: circumference,
      points_used: windowPts.length,
    });
  }

  if (!slices.length) return null;
  slices.sort((a, b) => a.position_mm - b.position_mm);
  if (typeof yMin === 'number' || typeof yMax === 'number') {
    const yMinVal = typeof yMin === 'number' ? yMin : slices[0].position_mm;
    const yMaxVal = typeof yMax === 'number' ? yMax : slices[slices.length - 1].position_mm;
    if (yMinVal < slices[0].position_mm - 1e-6) {
      const s0 = slices[0];
      slices.unshift({
        position_mm: yMinVal,
        center_mm: s0.center_mm,
        radius_mm: s0.radius_mm,
        diameter_mm: s0.diameter_mm,
        area_mm2: s0.area_mm2,
        circumference_mm: s0.circumference_mm,
        points_used: 0,
      });
    }
    if (yMaxVal > slices[slices.length - 1].position_mm + 1e-6) {
      const s1 = slices[slices.length - 1];
      slices.push({
        position_mm: yMaxVal,
        center_mm: s1.center_mm,
        radius_mm: s1.radius_mm,
        diameter_mm: s1.diameter_mm,
        area_mm2: s1.area_mm2,
        circumference_mm: s1.circumference_mm,
        points_used: 0,
      });
    }
  }
  const diameters = slices.map((s) => s.diameter_mm);
  const areas = slices.map((s) => s.area_mm2);
  const positions = slices.map((s) => s.position_mm);
  let volumeMm3 = 0;
  for (let i = 0; i < slices.length - 1; i += 1) {
    const dx = positions[i + 1] - positions[i];
    volumeMm3 += (areas[i] + areas[i + 1]) * 0.5 * dx;
  }
  let totalLengthMm = positions.length > 1 ? positions[positions.length - 1] - positions[0] : 0;
  if (typeof yMin === 'number' || typeof yMax === 'number') {
    const yMinVal = typeof yMin === 'number' ? yMin : positions[0];
    const yMaxVal = typeof yMax === 'number' ? yMax : positions[positions.length - 1];
    totalLengthMm = yMaxVal - yMinVal;
  }
  return {
    total_slices: slices.length,
    total_length_mm: totalLengthMm,
    volume_mm3: volumeMm3,
    volume_m3: volumeMm3 / 1e9,
    diameter_mm: {
      min: Math.min(...diameters),
      max: Math.max(...diameters),
      avg: diameters.reduce((a, b) => a + b, 0) / diameters.length,
    },
    slices,
  };
}

export default function AnalyticsPage() {
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
  const [analysis, setAnalysis] = useState<LogMetrics | ConveyorMetrics | null>(null);
  const [analysisApp, setAnalysisApp] = useState<AnalysisApp>('log');
  const [augmentedPoints, setAugmentedPoints] = useState<number[][]>([]);
  const [showAugmented, setShowAugmented] = useState(true);
  const [showOriginal, setShowOriginal] = useState(true);
  const [colorBySource, setColorBySource] = useState(true);
  const [analysisDuration, setAnalysisDuration] = useState<number | null>(null);
  const [colorMode, setColorMode] = useState<'rssi' | 'x' | 'y' | 'z'>('rssi');
  const [showFloor, setShowFloor] = useState(true);
  const [showConveyorBbox, setShowConveyorBbox] = useState(true);
  const [windowProfiles, setWindowProfiles] = useState(10);
  const [minPoints, setMinPoints] = useState(50);
  const [convPlaneQuantile, setConvPlaneQuantile] = useState(0.35);
  const [convPlaneInlierMm, setConvPlaneInlierMm] = useState(8);
  const [convObjectMinHeightMm, setConvObjectMinHeightMm] = useState(8);
  const [convObjectMaxPoints, setConvObjectMaxPoints] = useState(60000);
  const [convLocalizationAlgorithm, setConvLocalizationAlgorithm] = useState<ConveyorLocalizationAlgorithm>('object_cloud_bbox');
  const [convTopPlaneQuantile, setConvTopPlaneQuantile] = useState(0.88);
  const [convTopPlaneInlierMm, setConvTopPlaneInlierMm] = useState(4);
  const [convDenoiseEnabled, setConvDenoiseEnabled] = useState(true);
  const [convDenoiseCellMm, setConvDenoiseCellMm] = useState(8);
  const [convDenoiseMinPtsCell, setConvDenoiseMinPtsCell] = useState(3);
  const [convKeepLargestComponent, setConvKeepLargestComponent] = useState(true);
  const [analysisSaving, setAnalysisSaving] = useState(false);
  const [analysisSavedAt, setAnalysisSavedAt] = useState<number | null>(null);
  const [profilingDistance, setProfilingDistance] = useState<number>(10);
  const viewerRef = useRef<PointCloudThreeViewerHandle | null>(null);
  const fitOnceRef = useRef(false);

  const fetchStatus = async () => {
    const res = await api.get('/acquisition/trigger/status');
    setStatus(res.data);
    if (typeof res.data?.profiling_distance_mm === 'number') {
      setProfilingDistance(res.data.profiling_distance_mm);
    }
  };

  const fetchMotion = async () => {
    try {
      const res = await api.get('/calibration/motion-settings');
      if (typeof res.data?.profiling_distance_mm === 'number') {
        setProfilingDistance(res.data.profiling_distance_mm);
      }
    } catch {
      // ignore
    }
  };

  const fetchAnalysisSettings = async () => {
    try {
      const res = await api.get('/calibration/analysis-settings');
      const app = res.data?.active_app === 'conveyor_object' ? 'conveyor_object' : 'log';
      setAnalysisApp(app);
      if (typeof res.data?.log_window_profiles === 'number') setWindowProfiles(res.data.log_window_profiles);
      if (typeof res.data?.log_min_points === 'number') setMinPoints(res.data.log_min_points);
      if (typeof res.data?.conveyor_plane_quantile === 'number') setConvPlaneQuantile(res.data.conveyor_plane_quantile);
      if (typeof res.data?.conveyor_plane_inlier_mm === 'number') setConvPlaneInlierMm(res.data.conveyor_plane_inlier_mm);
      if (typeof res.data?.conveyor_object_min_height_mm === 'number') setConvObjectMinHeightMm(res.data.conveyor_object_min_height_mm);
      if (typeof res.data?.conveyor_object_max_points === 'number') setConvObjectMaxPoints(res.data.conveyor_object_max_points);
      const locAlgo = res.data?.conveyor_localization_algorithm === 'box_top_plane' ? 'box_top_plane' : 'object_cloud_bbox';
      setConvLocalizationAlgorithm(locAlgo);
      if (typeof res.data?.conveyor_top_plane_quantile === 'number') setConvTopPlaneQuantile(res.data.conveyor_top_plane_quantile);
      if (typeof res.data?.conveyor_top_plane_inlier_mm === 'number') setConvTopPlaneInlierMm(res.data.conveyor_top_plane_inlier_mm);
      if (typeof res.data?.conveyor_denoise_enabled === 'boolean') setConvDenoiseEnabled(res.data.conveyor_denoise_enabled);
      if (typeof res.data?.conveyor_denoise_cell_mm === 'number') setConvDenoiseCellMm(res.data.conveyor_denoise_cell_mm);
      if (typeof res.data?.conveyor_denoise_min_points_per_cell === 'number') setConvDenoiseMinPtsCell(res.data.conveyor_denoise_min_points_per_cell);
      if (typeof res.data?.conveyor_keep_largest_component === 'boolean') setConvKeepLargestComponent(res.data.conveyor_keep_largest_component);
    } catch {
      // ignore
    }
  };

  const handleSaveAndRecomputeAnalysis = async () => {
    setAnalysisSaving(true);
    try {
      await api.put('/calibration/analysis-settings', {
        active_app: analysisApp,
        log_window_profiles: windowProfiles,
        log_min_points: minPoints,
        conveyor_plane_quantile: convPlaneQuantile,
        conveyor_plane_inlier_mm: convPlaneInlierMm,
        conveyor_object_min_height_mm: convObjectMinHeightMm,
        conveyor_object_max_points: convObjectMaxPoints,
        conveyor_localization_algorithm: convLocalizationAlgorithm,
        conveyor_top_plane_quantile: convTopPlaneQuantile,
        conveyor_top_plane_inlier_mm: convTopPlaneInlierMm,
        conveyor_denoise_enabled: convDenoiseEnabled,
        conveyor_denoise_cell_mm: convDenoiseCellMm,
        conveyor_denoise_min_points_per_cell: convDenoiseMinPtsCell,
        conveyor_keep_largest_component: convKeepLargestComponent,
      });
      await api.post('/acquisition/analytics/recompute');
      await fetchAnalysis();
      await fetchLatest(fullCloud);
      setAnalysisSavedAt(Date.now());
    } finally {
      setAnalysisSaving(false);
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
    api.get('/acquisition/trigger/latest-cloud', {
      params: { max_points: 30000 },
    }).then((res) => {
      setPoints(res.data?.points || []);
      fitOnceRef.current = false;
      setHoverPoint(null);
      setViewerKey((v) => v + 1);
    }).catch(() => {
      // ignore
    });
    fetchMotion();
    fetchAnalysisSettings();
    fetchAnalysis();
    const interval = setInterval(() => {
      fetchStatus();
    }, 3000);
    return () => clearInterval(interval);
  }, []);

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
    setViewerKey((v) => v + 1);
  }, [showFloor]);

  const handleStart = async () => {
    setLoading(true);
    try {
      await api.post('/acquisition/trigger/start');
      setPoints([]);
      setAnalysis(null);
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
      await fetchLatest(true);
      await fetchAnalysis();
    } finally {
      setLoading(false);
    }
  };

  const fetchAnalysis = async () => {
    try {
      const res = await api.get('/acquisition/analytics/results');
      const responseApp = res.data?.analysis_app === 'conveyor_object' ? 'conveyor_object' : 'log';
      setAnalysisApp(responseApp);
      setAnalysis(res.data?.metrics || null);
      if (res.data?.has_points) {
        const pc = await api.get('/acquisition/analytics/augmented-cloud', {
          params: { max_points: 60000 },
        });
        setAugmentedPoints(pc.data?.points || []);
      } else {
        setAugmentedPoints([]);
      }
      if (typeof res.data?.analysis_duration_ms === 'number') {
        setAnalysisDuration(res.data.analysis_duration_ms);
      }
    } catch {
      setAnalysis(null);
      setAugmentedPoints([]);
      setAnalysisDuration(null);
    }
  };

  const handleAnalyze = () => {
    if (analysisApp !== 'log') {
      return;
    }
    const result = computeLogMetrics(
      points,
      profilingDistance,
      windowProfiles,
      minPoints,
      yStats?.minY,
      yStats?.maxY
    );
    setAnalysis(result);
  };

  const speedLabel = status.speed_mps !== null
    ? `${status.speed_mps.toFixed(2)} m/s`
    : 'Encoder';

  const distanceM = status.distance_mm / 1000.0;

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

  const transformedOriginalPoints = useMemo(
    () => points.map((p) => mapPointToViewer(p)),
    [points]
  );

  const transformedAugmentedPoints = useMemo(
    () => augmentedPoints.map((p) => mapPointToViewer(p)),
    [augmentedPoints]
  );

  const displayPoints = useMemo(() => {
    const originals = showOriginal ? transformedOriginalPoints : [];
    const augmented = showAugmented ? transformedAugmentedPoints : [];
    if (colorBySource) {
      const origTagged = originals.map((p) => [p[0], p[1], p[2], 20]);
      const augTagged = augmented.map((p) => [p[0], p[1], p[2], 90]);
      return [...origTagged, ...augTagged];
    }
    return [...originals, ...augmented];
  }, [transformedOriginalPoints, transformedAugmentedPoints, showAugmented, showOriginal, colorBySource]);

  const yStats = useMemo(() => {
    if (!points || points.length === 0) return null;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const p of points) {
      if (p[1] < minY) minY = p[1];
      if (p[1] > maxY) maxY = p[1];
    }
    return { minY, maxY, length: maxY - minY };
  }, [points]);

  const logAnalysis = useMemo(() => (isLogMetrics(analysis) ? analysis : null), [analysis]);
  const conveyorAnalysis = useMemo(() => (isConveyorMetrics(analysis) ? analysis : null), [analysis]);

  const edgeDiameters = useMemo(() => {
    if (!logAnalysis || !logAnalysis.slices || logAnalysis.slices.length === 0) return null;
    const first = logAnalysis.slices[0];
    const last = logAnalysis.slices[logAnalysis.slices.length - 1];
    return {
      startPos: first.position_mm,
      startDia: first.diameter_mm,
      endPos: last.position_mm,
      endDia: last.diameter_mm,
    };
  }, [logAnalysis]);

  const bounds = useMemo(() => {
    if (!transformedOriginalPoints || transformedOriginalPoints.length === 0) return null;
    let minX = Infinity;
    let minY = Infinity;
    let minZ = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    let maxZ = -Infinity;
    for (const p of transformedOriginalPoints) {
      if (p[0] < minX) minX = p[0];
      if (p[1] < minY) minY = p[1];
      if (p[2] < minZ) minZ = p[2];
      if (p[0] > maxX) maxX = p[0];
      if (p[1] > maxY) maxY = p[1];
      if (p[2] > maxZ) maxZ = p[2];
    }
    return { minX, minY, minZ, maxX, maxY, maxZ };
  }, [transformedOriginalPoints]);

  const annotations = useMemo(() => {
    if (analysisApp === 'conveyor_object') {
      if (!showConveyorBbox) return [];
      if (!transformedAugmentedPoints || transformedAugmentedPoints.length < 3) return [];
      let minX = Infinity;
      let minY = Infinity;
      let minZ = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      let maxZ = -Infinity;
      for (const p of transformedAugmentedPoints) {
        if (p[0] < minX) minX = p[0];
        if (p[1] < minY) minY = p[1];
        if (p[2] < minZ) minZ = p[2];
        if (p[0] > maxX) maxX = p[0];
        if (p[1] > maxY) maxY = p[1];
        if (p[2] > maxZ) maxZ = p[2];
      }
      return buildAabbAnnotations(minX, maxX, minY, maxY, minZ, maxZ);
    }

    if (analysisApp !== 'log') return [];
    if (!yStats || !bounds) return [];
    const xRef = bounds.maxX + (bounds.maxX - bounds.minX) * 0.12;
    const zBase = bounds.minZ + (bounds.maxZ - bounds.minZ) * 0.6;
    const startY = yStats.minY;
    const dataLen = yStats.length;
    const ann: {
      start: [number, number, number];
      end: [number, number, number];
      color?: number;
      label?: string;
    }[] = [];

    if (dataLen > 0) {
      ann.push({
        start: [xRef + 80, startY, zBase + 60] as [number, number, number],
        end: [xRef + 80, startY + dataLen, zBase + 60] as [number, number, number],
        label: `Length ${(dataLen / 1000).toFixed(2)} m`,
        color: 0xef4444,
      });
    }

    if (edgeDiameters) {
      const dx = (bounds.maxX - bounds.minX) * 0.15;
      ann.push({
        start: [xRef - dx, edgeDiameters.startPos, zBase] as [number, number, number],
        end: [xRef, edgeDiameters.startPos, zBase] as [number, number, number],
        label: `Start Ø ${edgeDiameters.startDia.toFixed(0)} mm`,
        color: 0xef4444,
      });
      ann.push({
        start: [xRef - dx, edgeDiameters.endPos, zBase] as [number, number, number],
        end: [xRef, edgeDiameters.endPos, zBase] as [number, number, number],
        label: `End Ø ${edgeDiameters.endDia.toFixed(0)} mm`,
        color: 0xef4444,
      });
    }
    return ann;
  }, [analysisApp, yStats, bounds, edgeDiameters, transformedAugmentedPoints, showConveyorBbox]);

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-slate-900">Analytics</h1>
            <p className="text-sm text-gray-500 mt-1">
              Active app: {analysisApp === 'conveyor_object' ? 'Conveyor object measurement' : 'Log measurement'}
            </p>
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
                <p className="text-xs text-gray-500">Last registered point cloud (unified frame)</p>
              </div>
              <div className="flex items-center gap-2 text-xs text-gray-500">
                <span>{status.points_count} points</span>
                {augmentedPoints.length > 0 && (
                  <label className="flex items-center gap-2 text-[11px] text-gray-500">
                    <input
                      type="checkbox"
                      checked={showAugmented}
                      onChange={(e) => setShowAugmented(e.target.checked)}
                    />
                    Augmented
                  </label>
                )}
                <label className="flex items-center gap-2 text-[11px] text-gray-500">
                  <input
                    type="checkbox"
                    checked={showOriginal}
                    onChange={(e) => setShowOriginal(e.target.checked)}
                  />
                  Original
                </label>
                <label className="flex items-center gap-2 text-[11px] text-gray-500">
                  <input
                    type="checkbox"
                    checked={colorBySource}
                    onChange={(e) => setColorBySource(e.target.checked)}
                  />
                  Color by source
                </label>
                <label className="flex items-center gap-2 text-[11px] text-gray-500">
                  <input
                    type="checkbox"
                    checked={fullCloud}
                    onChange={(e) => setFullCloud(e.target.checked)}
                  />
                  Full cloud
                </label>
                {analysisApp === 'conveyor_object' && (
                  <label className="flex items-center gap-2 text-[11px] text-gray-500">
                    <input
                      type="checkbox"
                      checked={showConveyorBbox}
                      onChange={(e) => setShowConveyorBbox(e.target.checked)}
                    />
                    Show BBox
                  </label>
                )}
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
                  annotations={annotations}
                  width="100%"
                height="100%"
                showGrid
                gridSize={12000}
                gridStep={1000}
                colorScaleMode={colorBySource ? 'rssi100' : (rssiStats ? 'rssi100' : 'auto')}
                colorMode={colorMode}
                onHoverPoint={setHoverPoint}
                showOriginAxes
                originAxisSize={1000}
                showFloor={showFloor}
              />
              <div className="absolute top-4 left-4 flex items-center gap-2 rounded-md bg-black/40 px-2 py-1 text-xs text-white">
                <span className="text-gray-200">Color</span>
                <select
                  className="bg-transparent text-white text-xs outline-none"
                  style={{ backgroundColor: 'rgba(15,23,42,0.9)' }}
                  value={colorMode}
                  onChange={(e) => setColorMode(e.target.value as typeof colorMode)}
                >
                  <option style={{ backgroundColor: '#0f172a', color: '#e2e8f0' }} value="rssi">RSSI</option>
                  <option style={{ backgroundColor: '#0f172a', color: '#e2e8f0' }} value="x">X</option>
                  <option style={{ backgroundColor: '#0f172a', color: '#e2e8f0' }} value="y">Y</option>
                  <option style={{ backgroundColor: '#0f172a', color: '#e2e8f0' }} value="z">Z</option>
                </select>
              </div>
              <div className="absolute top-4 right-4 flex items-center gap-2 rounded-md bg-black/40 px-2 py-1 text-xs text-white">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={showFloor}
                    onChange={(e) => setShowFloor(e.target.checked)}
                  />
                  Floor
                </label>
              </div>
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
          </div>

          <div className="space-y-6">
            <div className="card">
              <h2 className="text-xl font-semibold text-slate-900">Live Metrics</h2>
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
                  <p className="text-2xl font-semibold text-slate-900">{profilingDistance} mm</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Profiles collected</p>
                  <p className="text-2xl font-semibold text-slate-900">{status.profiles_count ?? 0}</p>
                </div>
              </div>
              <div className="mt-4 text-xs text-gray-500">
                Last update: {status.last_update_ts ? new Date(status.last_update_ts * 1000).toLocaleTimeString() : ''}
              </div>
            </div>

            <div className="card">
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-slate-900">Analysis Results</h2>
                <button
                  className="btn-secondary"
                  onClick={handleSaveAndRecomputeAnalysis}
                  disabled={analysisSaving || status.recording}
                  title={status.recording ? 'Stop acquisition before recompute' : ''}
                >
                  {analysisSaving ? 'Saving...' : 'Save & Recompute'}
                </button>
              </div>
              {analysisApp === 'log' ? (
                <p className="text-xs text-gray-500 mt-1">
                  Fits a circle to each window on the X/Z plane. Length is along Y.
                </p>
              ) : (
                <p className="text-xs text-gray-500 mt-1">
                  Fits conveyor plane and dimensions object points above the plane.
                </p>
              )}

              {analysisApp === 'log' && (
                <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-gray-500">Active app</span>
                    <select className="input" value={analysisApp} onChange={(e) => setAnalysisApp(e.target.value as AnalysisApp)}>
                      <option value="log">Log measurement</option>
                      <option value="conveyor_object">Conveyor object</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-gray-500">Window size (profiles)</span>
                    <input
                      className="input"
                      type="number"
                      min={1}
                      value={windowProfiles}
                      onChange={(e) => setWindowProfiles(Number(e.target.value || 1))}
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-gray-500">Min points per window</span>
                    <input
                      className="input"
                      type="number"
                      min={10}
                      value={minPoints}
                      onChange={(e) => setMinPoints(Number(e.target.value || 10))}
                    />
                  </label>
                </div>
              )}

              {analysisApp === 'conveyor_object' && (
                <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-gray-500">Active app</span>
                    <select className="input" value={analysisApp} onChange={(e) => setAnalysisApp(e.target.value as AnalysisApp)}>
                      <option value="log">Log measurement</option>
                      <option value="conveyor_object">Conveyor object</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-gray-500">Localization algorithm</span>
                    <select
                      className="input"
                      value={convLocalizationAlgorithm}
                      onChange={(e) => setConvLocalizationAlgorithm(e.target.value as ConveyorLocalizationAlgorithm)}
                    >
                      <option value="object_cloud_bbox">Object cloud bbox</option>
                      <option value="box_top_plane">Box top plane</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-gray-500">Plane quantile</span>
                    <input className="input" type="number" step={0.01} min={0.05} max={0.8} value={convPlaneQuantile} onChange={(e) => setConvPlaneQuantile(Number(e.target.value || 0.35))} />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-gray-500">Plane inlier [mm]</span>
                    <input className="input" type="number" step={0.5} min={1} value={convPlaneInlierMm} onChange={(e) => setConvPlaneInlierMm(Number(e.target.value || 8))} />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-gray-500">Object min height [mm]</span>
                    <input className="input" type="number" step={0.5} min={1} value={convObjectMinHeightMm} onChange={(e) => setConvObjectMinHeightMm(Number(e.target.value || 8))} />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-gray-500">Augmented max points</span>
                    <input className="input" type="number" min={1000} step={1000} value={convObjectMaxPoints} onChange={(e) => setConvObjectMaxPoints(Number(e.target.value || 60000))} />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-gray-500">Denoise cell [mm]</span>
                    <input className="input" type="number" step={0.5} min={2} value={convDenoiseCellMm} onChange={(e) => setConvDenoiseCellMm(Number(e.target.value || 8))} />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-gray-500">Denoise min pts/cell</span>
                    <input className="input" type="number" min={1} step={1} value={convDenoiseMinPtsCell} onChange={(e) => setConvDenoiseMinPtsCell(Number(e.target.value || 3))} />
                  </label>
                  {convLocalizationAlgorithm === 'box_top_plane' && (
                    <>
                      <label className="flex flex-col gap-1">
                        <span className="text-xs text-gray-500">Top plane quantile</span>
                        <input
                          className="input"
                          type="number"
                          step={0.01}
                          min={0.6}
                          max={0.99}
                          value={convTopPlaneQuantile}
                          onChange={(e) => setConvTopPlaneQuantile(Number(e.target.value || 0.88))}
                        />
                      </label>
                      <label className="flex flex-col gap-1">
                        <span className="text-xs text-gray-500">Top plane inlier [mm]</span>
                        <input
                          className="input"
                          type="number"
                          step={0.5}
                          min={0.5}
                          value={convTopPlaneInlierMm}
                          onChange={(e) => setConvTopPlaneInlierMm(Number(e.target.value || 4))}
                        />
                      </label>
                    </>
                  )}
                  <label className="flex items-center gap-2 text-xs text-gray-500 mt-6">
                    <input type="checkbox" checked={convDenoiseEnabled} onChange={(e) => setConvDenoiseEnabled(e.target.checked)} />
                    Denoise enabled
                  </label>
                  <label className="flex items-center gap-2 text-xs text-gray-500 mt-6">
                    <input type="checkbox" checked={convKeepLargestComponent} onChange={(e) => setConvKeepLargestComponent(e.target.checked)} />
                    Keep largest component
                  </label>
                </div>
              )}

              {analysisSavedAt !== null && (
                <div className="mt-2 text-xs text-emerald-600">
                  Saved {new Date(analysisSavedAt).toLocaleTimeString()}
                </div>
              )}

              <div className="mt-4 flex gap-2">
                {analysisApp === 'log' && (
                  <button className="btn-primary" onClick={handleAnalyze} disabled={loading || status.recording}>
                    <Wand2 className="h-4 w-4" />
                    Run local analysis
                  </button>
                )}
                <button className="btn-secondary" onClick={fetchAnalysis} disabled={loading}>
                  Fetch server analysis
                </button>
                <button className="btn-secondary" onClick={() => setAnalysis(null)}>
                  Clear
                </button>
              </div>

              {analysisDuration !== null && (
                <div className="mt-3 text-xs text-gray-500">Analysis time: {analysisDuration} ms</div>
              )}

              {logAnalysis && (
                <div className="mt-4 space-y-3 text-sm text-gray-500">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <div className="text-xs text-gray-400">Slices</div>
                      <div className="text-lg font-semibold text-slate-900">{logAnalysis.total_slices}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">Length</div>
                      <div className="text-lg font-semibold text-slate-900">{(logAnalysis.total_length_mm / 1000).toFixed(2)} m</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">Diameter avg</div>
                      <div className="text-lg font-semibold text-slate-900">{logAnalysis.diameter_mm.avg.toFixed(1)} mm</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">Diameter min / max</div>
                      <div className="text-lg font-semibold text-slate-900">
                        {logAnalysis.diameter_mm.min.toFixed(1)} / {logAnalysis.diameter_mm.max.toFixed(1)} mm
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">Volume</div>
                      <div className="text-lg font-semibold text-slate-900">{logAnalysis.volume_m3.toFixed(6)} m³</div>
                    </div>
                  </div>
                  <div className="text-xs text-gray-400">Latest slices</div>
                  <div className="max-h-48 overflow-auto rounded-md border border-slate-200">
                    <table className="w-full text-xs">
                      <thead className="bg-slate-50 text-gray-500">
                        <tr>
                          <th className="px-2 py-1 text-left">pos [mm]</th>
                          <th className="px-2 py-1 text-left">diam [mm]</th>
                          <th className="px-2 py-1 text-left">circ [mm]</th>
                          <th className="px-2 py-1 text-left">pts</th>
                        </tr>
                      </thead>
                      <tbody>
                        {logAnalysis.slices.slice(-12).map((s) => (
                          <tr key={`${s.position_mm}-${s.points_used}`} className="border-t border-slate-100">
                            <td className="px-2 py-1">{s.position_mm.toFixed(1)}</td>
                            <td className="px-2 py-1">{s.diameter_mm.toFixed(1)}</td>
                            <td className="px-2 py-1">{s.circumference_mm.toFixed(1)}</td>
                            <td className="px-2 py-1">{s.points_used}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {conveyorAnalysis && (
                <div className="mt-4 space-y-3 text-sm text-gray-500">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <div className="text-xs text-gray-400">Localization</div>
                      <div className="text-lg font-semibold text-slate-900">
                        {conveyorAnalysis.object?.localization_algorithm === 'box_top_plane' ? 'box_top_plane' : 'object_cloud_bbox'}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">Object points</div>
                      <div className="text-lg font-semibold text-slate-900">{conveyorAnalysis.object?.points_count ?? 0}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">Plane inliers</div>
                      <div className="text-lg font-semibold text-slate-900">{conveyorAnalysis.plane?.inliers_count ?? 0}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">BBox L x W x H</div>
                      <div className="text-lg font-semibold text-slate-900">
                        {(conveyorAnalysis.object?.bbox_mm?.length ?? 0).toFixed(1)} x {(conveyorAnalysis.object?.bbox_mm?.width ?? 0).toFixed(1)} x {(conveyorAnalysis.object?.bbox_mm?.height ?? 0).toFixed(1)} mm
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">Volume</div>
                      <div className="text-lg font-semibold text-slate-900">
                        {(conveyorAnalysis.object?.bbox_volume_m3 ?? 0).toFixed(6)} m³
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">Height avg / max</div>
                      <div className="text-lg font-semibold text-slate-900">
                        {(conveyorAnalysis.object?.height_above_plane_mm?.avg ?? 0).toFixed(1)} / {(conveyorAnalysis.object?.height_above_plane_mm?.max ?? 0).toFixed(1)} mm
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">Plane RMSE</div>
                      <div className="text-lg font-semibold text-slate-900">
                        {conveyorAnalysis.plane?.rmse_mm !== undefined && conveyorAnalysis.plane?.rmse_mm !== null
                          ? `${conveyorAnalysis.plane.rmse_mm.toFixed(2)} mm`
                          : 'n/a'}
                      </div>
                    </div>
                    {conveyorAnalysis.object?.top_plane && (
                      <div>
                        <div className="text-xs text-gray-400">Top plane angle</div>
                        <div className="text-lg font-semibold text-slate-900">
                          {conveyorAnalysis.object.top_plane.footprint_angle_deg !== undefined
                            ? `${conveyorAnalysis.object.top_plane.footprint_angle_deg.toFixed(1)} deg`
                            : 'n/a'}
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="text-xs text-gray-400">Object centroid [x, y, z] mm</div>
                  <div className="rounded-md border border-slate-200 px-3 py-2 text-xs font-mono text-slate-700">
                    {conveyorAnalysis.object?.centroid_mm
                      ? `${conveyorAnalysis.object.centroid_mm[0].toFixed(1)}, ${conveyorAnalysis.object.centroid_mm[1].toFixed(1)}, ${conveyorAnalysis.object.centroid_mm[2].toFixed(1)}`
                      : 'n/a'}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </Layout>
  );
}
