'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import Layout from '@/components/layout/Layout';
import api from '@/services/api';
import PointCloudThreeViewer, { type PointCloudThreeViewerHandle } from '@/components/visualization/PointCloudThreeViewer';
import { SynInteropButton, SynInteropDropdown } from '@/components/synergy/SynInterop';
import { Play, Square, RefreshCw, Wand2, Plus, Trash2, ArrowUp, ArrowDown, X } from 'lucide-react';

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

interface OutputSettings {
  enabled: boolean;
  connection_mode: 'server' | 'client';
  host: string;
  port: number;
  payload_mode: 'ascii' | 'json';
  separator: string;
  prefix: string;
  suffix: string;
  include_labels: boolean;
  float_precision: number;
  length_unit: 'mm' | 'm';
  volume_unit: 'm3' | 'l' | 'mm3';
  selected_fields: string[];
  output_frame_items?: OutputFrameItem[];
}

interface OutputFrameItem {
  type: 'field' | 'text' | 'marker';
  key?: string;
  label?: string;
  text?: string;
  value?: string;
  precision?: number;
}

interface OutputPreviewResponse {
  source: 'session' | 'history' | string;
  analysis_app: string;
  has_metrics: boolean;
  payload: string | Record<string, unknown>;
  timestamp_ms?: number | null;
}

type AnalysisApp = 'none' | 'log' | 'conveyor_object';
type ConveyorLocalizationAlgorithm = 'object_cloud_bbox' | 'box_top_plane';

const OUTPUT_FIELD_COMMON: Array<{ key: string; label: string }> = [
  { key: 'timestamp_iso', label: 'Timestamp ISO' },
  { key: 'timestamp_ms', label: 'Timestamp ms' },
  { key: 'analysis_app', label: 'Analysis app' },
  { key: 'distance_mm', label: 'Distance [mm]' },
  { key: 'profiles_count', label: 'Profiles count' },
  { key: 'unit_length', label: 'Unit length' },
  { key: 'unit_volume', label: 'Unit volume' },
];

const OUTPUT_FIELD_LOG: Array<{ key: string; label: string }> = [
  { key: 'volume', label: 'Volume (selected unit)' },
  { key: 'length', label: 'Length (selected unit)' },
  { key: 'diameter_start', label: 'Diameter start (selected unit)' },
  { key: 'diameter_end', label: 'Diameter end (selected unit)' },
  { key: 'diameter_avg', label: 'Diameter avg (selected unit)' },
  { key: 'diameter_min', label: 'Diameter min (selected unit)' },
  { key: 'diameter_max', label: 'Diameter max (selected unit)' },
];

const LENGTH_BASE_KEYS = new Set([
  'length',
  'diameter_start',
  'diameter_end',
  'diameter_avg',
  'diameter_min',
  'diameter_max',
  'object_bbox_length',
  'object_bbox_width',
  'object_bbox_height',
]);

const VOLUME_BASE_KEYS = new Set([
  'volume',
  'object_bbox_volume',
]);

type OutputUnitChoice = 'default' | 'mm' | 'm' | 'm3' | 'l' | 'mm3';

const OUTPUT_SEPARATOR_OPTIONS: Array<{ value: string; label: string }> = [
  { value: ';', label: '; semicolon' },
  { value: ',', label: ', comma' },
  { value: '|', label: '| pipe' },
  { value: '\t', label: 'TAB (\\t)' },
  { value: ' ', label: 'SPACE' },
];

const OUTPUT_MARKER_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '\u0002', label: 'STX (0x02)' },
  { value: '\u0003', label: 'ETX (0x03)' },
  { value: '\r', label: 'CR (\\r)' },
  { value: '\n', label: 'LF (\\n)' },
  { value: '\r\n', label: 'CRLF (\\r\\n)' },
  { value: '\t', label: 'TAB (\\t)' },
  { value: '|', label: 'Pipe |' },
];

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

function resolveFieldKeyWithUnit(baseKey: string, unit: OutputUnitChoice): string {
  if (LENGTH_BASE_KEYS.has(baseKey)) {
    if (unit === 'mm') return `${baseKey}_mm`;
    if (unit === 'm') return `${baseKey}_m`;
    return baseKey;
  }
  if (VOLUME_BASE_KEYS.has(baseKey)) {
    if (unit === 'm3') return `${baseKey}_m3`;
    if (unit === 'l') return `${baseKey}_l`;
    if (unit === 'mm3') return `${baseKey}_mm3`;
    return baseKey;
  }
  return baseKey;
}

function humanizeFrameItem(item: OutputFrameItem): string {
  if (item.type === 'text') return item.text || '(text)';
  if (item.type === 'marker') {
    const preset = OUTPUT_MARKER_OPTIONS.find((m) => m.value === (item.value || ''));
    return preset?.label || item.value || '(marker)';
  }
  const lbl = (item.label || '').trim();
  if (lbl) return lbl;
  return item.key || '(field)';
}

function visualizeControlChars(input: string): string {
  return String(input || '')
    .replace(/\u0002/g, '[STX]')
    .replace(/\u0003/g, '[ETX]')
    .replace(/\r\n/g, '[CRLF]')
    .replace(/\r/g, '[CR]')
    .replace(/\n/g, '[LF]')
    .replace(/\t/g, '[TAB]');
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
  const [analysisTimestampMs, setAnalysisTimestampMs] = useState<number | null>(null);
  const [augmentedPoints, setAugmentedPoints] = useState<number[][]>([]);
  const [showAugmented, setShowAugmented] = useState(true);
  const [showOriginal, setShowOriginal] = useState(true);
  const [colorBySource, setColorBySource] = useState(true);
  const [analysisDuration, setAnalysisDuration] = useState<number | null>(null);
  const [colorMode, setColorMode] = useState<'rssi' | 'x' | 'y' | 'z'>('rssi');
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
  const [outputSaving, setOutputSaving] = useState(false);
  const [outputSavedAt, setOutputSavedAt] = useState<number | null>(null);
  const [outputWizardOpen, setOutputWizardOpen] = useState(false);
  const [outputFieldToAdd, setOutputFieldToAdd] = useState<string>('timestamp_iso');
  const [outputFieldUnit, setOutputFieldUnit] = useState<OutputUnitChoice>('default');
  const [outputFieldPrecision, setOutputFieldPrecision] = useState<string>('2');
  const [outputCustomText, setOutputCustomText] = useState('');
  const [outputMarkerPreset, setOutputMarkerPreset] = useState<string>('\u0002');
  const [outputMarkerCustom, setOutputMarkerCustom] = useState('');
  const [outputServerPreview, setOutputServerPreview] = useState<OutputPreviewResponse | null>(null);
  const [outputSettings, setOutputSettings] = useState<OutputSettings>({
    enabled: false,
    connection_mode: 'server',
    host: '0.0.0.0',
    port: 2120,
    payload_mode: 'ascii',
    separator: ';',
    prefix: '',
    suffix: '',
    include_labels: false,
    float_precision: 2,
    length_unit: 'mm',
    volume_unit: 'm3',
    selected_fields: ['timestamp_iso', 'analysis_app', 'volume', 'length', 'diameter_start', 'diameter_end', 'diameter_avg'],
    output_frame_items: [],
  });
  const [profilingDistance, setProfilingDistance] = useState<number>(10);
  const viewerRef = useRef<PointCloudThreeViewerHandle | null>(null);
  const fitOnceRef = useRef(false);
  const lastAnalysisTsRef = useRef<number | null>(null);

  const outputFieldOptions = useMemo(() => {
    if (analysisApp === 'log') {
      return [...OUTPUT_FIELD_COMMON, ...OUTPUT_FIELD_LOG];
    }
    return [...OUTPUT_FIELD_COMMON];
  }, [analysisApp]);

  const outputAllowedFieldSet = useMemo(() => {
    const keys = new Set<string>(outputFieldOptions.map((f) => f.key));
    for (const f of outputFieldOptions) {
      if (LENGTH_BASE_KEYS.has(f.key)) {
        keys.add(`${f.key}_mm`);
        keys.add(`${f.key}_m`);
      }
      if (VOLUME_BASE_KEYS.has(f.key)) {
        keys.add(`${f.key}_m3`);
        keys.add(`${f.key}_l`);
        keys.add(`${f.key}_mm3`);
      }
    }
    return keys;
  }, [outputFieldOptions]);

  useEffect(() => {
    setOutputSettings((prev) => ({
      ...prev,
      selected_fields: prev.selected_fields.filter((k) => outputAllowedFieldSet.has(k)),
      output_frame_items: (prev.output_frame_items || []).filter((it) => {
        if (it.type === 'text') return true;
        if (it.type === 'marker') return true;
        return !!it.key && outputAllowedFieldSet.has(it.key);
      }),
    }));
  }, [outputAllowedFieldSet]);

  useEffect(() => {
    setOutputSettings((prev) => {
      const frameFields = (prev.output_frame_items || [])
        .filter((it) => it.type === 'field' && !!it.key)
        .map((it) => String(it.key));
      if (frameFields.length === 0) return prev;
      if (frameFields.length === prev.selected_fields.length && frameFields.every((v, i) => v === prev.selected_fields[i])) {
        return prev;
      }
      return { ...prev, selected_fields: frameFields };
    });
  }, [outputSettings.output_frame_items]);

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
      const app = String(res.data?.active_app || 'log').toLowerCase();
      setAnalysisApp(app === 'none' ? 'none' : 'log');
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

  const fetchOutputSettings = async () => {
    try {
      const res = await api.get('/calibration/output-settings');
      const d = res.data || {};
      setOutputSettings({
        enabled: !!d.enabled,
        connection_mode: 'server',
        host: String(d.host ?? '0.0.0.0'),
        port: 2120,
        payload_mode: d.payload_mode === 'json' ? 'json' : 'ascii',
        separator: String(d.separator ?? ';'),
        prefix: '',
        suffix: '',
        include_labels: !!d.include_labels,
        float_precision: Number(d.float_precision ?? 2),
        length_unit: d.length_unit === 'm' ? 'm' : 'mm',
        volume_unit: d.volume_unit === 'l' || d.volume_unit === 'mm3' ? d.volume_unit : 'm3',
        selected_fields: Array.isArray(d.selected_fields) ? d.selected_fields.map((v: unknown) => String(v)) : [],
        output_frame_items: Array.isArray(d.output_frame_items)
          ? d.output_frame_items
              .map((it: unknown) => {
                const item = (it || {}) as Record<string, unknown>;
                const type = item.type === 'text' ? 'text' : item.type === 'marker' ? 'marker' : 'field';
                return {
                  type,
                  key: typeof item.key === 'string' ? item.key : undefined,
                  label: typeof item.label === 'string' ? item.label : undefined,
                  text: typeof item.text === 'string' ? item.text : undefined,
                  value: typeof item.value === 'string' ? item.value : undefined,
                  precision: typeof item.precision === 'number' ? item.precision : undefined,
                } as OutputFrameItem;
              })
              .filter((it: OutputFrameItem) => (it.type === 'text' ? true : it.type === 'marker' ? true : !!it.key))
          : [],
      });
    } catch {
      // ignore
    }
  };

  const handleSaveOutputSettings = async () => {
    setOutputSaving(true);
    try {
      const frameItems = (outputSettings.output_frame_items || []).filter((it) => {
        if (it.type === 'text') return typeof it.text === 'string';
        if (it.type === 'marker') return typeof it.value === 'string' || typeof it.text === 'string';
        return typeof it.key === 'string' && it.key.length > 0;
      });
      await api.put('/calibration/output-settings', {
        ...outputSettings,
        connection_mode: 'server',
        port: 2120,
        payload_mode: 'ascii',
        include_labels: false,
        prefix: '',
        suffix: '',
        output_frame_items: frameItems,
      });
      setOutputSavedAt(Date.now());
      await fetchOutputPreview();
    } finally {
      setOutputSaving(false);
    }
  };

  const fetchOutputPreview = async () => {
    try {
      const res = await api.get('/acquisition/analytics/output-preview');
      setOutputServerPreview(res.data as OutputPreviewResponse);
    } catch {
      setOutputServerPreview(null);
    }
  };

  const addFieldToFrame = () => {
    const resolvedKey = resolveFieldKeyWithUnit(outputFieldToAdd, outputFieldUnit);
    const label = outputFieldOptions.find((f) => f.key === outputFieldToAdd)?.label || outputFieldToAdd;
    const parsedPrecision = Number(outputFieldPrecision);
    const precision = Number.isFinite(parsedPrecision) ? Math.max(0, Math.min(8, parsedPrecision)) : 2;
    setOutputSettings((prev) => ({
      ...prev,
      output_frame_items: [...(prev.output_frame_items || []), { type: 'field', key: resolvedKey, label, precision }],
    }));
  };

  const addTextToFrame = () => {
    const txt = outputCustomText;
    setOutputSettings((prev) => ({
      ...prev,
      output_frame_items: [...(prev.output_frame_items || []), { type: 'text', text: txt }],
    }));
    setOutputCustomText('');
  };

  const addMarkerToFrame = (value: string, label?: string) => {
    setOutputSettings((prev) => ({
      ...prev,
      output_frame_items: [...(prev.output_frame_items || []), { type: 'marker', value, label }],
    }));
  };

  const setFrameItemPrecision = (idx: number, nextPrecision: number) => {
    setOutputSettings((prev) => {
      const arr = [...(prev.output_frame_items || [])];
      const item = arr[idx];
      if (!item || item.type !== 'field') return prev;
      arr[idx] = { ...item, precision: Math.max(0, Math.min(8, nextPrecision)) };
      return { ...prev, output_frame_items: arr };
    });
  };

  const moveFrameItem = (idx: number, direction: -1 | 1) => {
    setOutputSettings((prev) => {
      const arr = [...(prev.output_frame_items || [])];
      const next = idx + direction;
      if (next < 0 || next >= arr.length) return prev;
      const tmp = arr[idx];
      arr[idx] = arr[next];
      arr[next] = tmp;
      return { ...prev, output_frame_items: arr };
    });
  };

  const removeFrameItem = (idx: number) => {
    setOutputSettings((prev) => ({
      ...prev,
      output_frame_items: (prev.output_frame_items || []).filter((_, i) => i !== idx),
    }));
  };

  const handleSaveAndRecomputeAnalysis = async () => {
    setAnalysisSaving(true);
    try {
      await api.put('/calibration/analysis-settings', {
        active_app: analysisApp === 'none' ? 'none' : 'log',
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
      await fetchAnalysis({ forceCloud: true });
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
    fetchOutputSettings();
    fetchOutputPreview();
    fetchAnalysis({ forceCloud: true });
    const interval = setInterval(() => {
      fetchStatus();
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!outputWizardOpen) return;
    void fetchOutputPreview();
  }, [outputWizardOpen]);

  useEffect(() => {
    const interval = setInterval(() => {
      if (status.recording) return;
      void fetchAnalysis();
    }, 1200);
    return () => clearInterval(interval);
  }, [status.recording]);

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
      await fetchAnalysis({ forceCloud: true });
    } finally {
      setLoading(false);
    }
  };

  const fetchAnalysis = async (opts?: { forceCloud?: boolean; full?: boolean }) => {
    try {
      const res = await api.get('/acquisition/analytics/results');
      setAnalysis(res.data?.metrics || null);
      const ts = typeof res.data?.analysis_timestamp_ms === 'number' ? res.data.analysis_timestamp_ms : null;
      const changed = ts !== null && ts !== lastAnalysisTsRef.current;
      setAnalysisTimestampMs(ts);
      if (ts !== null) {
        lastAnalysisTsRef.current = ts;
      }
      if (res.data?.has_points && (opts?.forceCloud || changed)) {
        const wantFull = !!opts?.full;
        const pc = await api.get('/acquisition/analytics/augmented-cloud', {
          params: { max_points: wantFull ? 0 : 60000 },
        });
        setAugmentedPoints(pc.data?.points || []);
      } else if (!res.data?.has_points) {
        setAugmentedPoints([]);
      }
      if (typeof res.data?.analysis_duration_ms === 'number') {
        setAnalysisDuration(res.data.analysis_duration_ms);
      }
    } catch {
      setAnalysis(null);
      setAnalysisTimestampMs(null);
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
  const colorModeLabel = colorMode.toUpperCase();
  const outputFramePreviewRaw = useMemo(() => {
    const items = outputSettings.output_frame_items || [];
    if (items.length === 0) return '(empty frame)';
    const sep = outputSettings.separator || ';';
    const parts = items.map((it) => {
      if (it.type === 'text') return { type: 'text', txt: it.text || '' };
      if (it.type === 'marker') return { type: 'marker', txt: it.value || it.text || '' };
      return { type: 'field', txt: it.label || it.key || '' };
    });
    const body: string[] = [];
    for (let i = 0; i < parts.length; i += 1) {
      const curr = parts[i];
      if (i > 0) {
        const prev = parts[i - 1];
        const prevIsLeadingMarker = i - 1 === 0 && prev.type === 'marker';
        const currIsTrailingMarker = i === parts.length - 1 && curr.type === 'marker';
        if (!prevIsLeadingMarker && !currIsTrailingMarker) {
          body.push(sep);
        }
      }
      body.push(curr.txt);
    }
    return `${outputSettings.prefix || ''}${body.join('')}${outputSettings.suffix || ''}`;
  }, [outputSettings.output_frame_items, outputSettings.separator, outputSettings.prefix, outputSettings.suffix]);
  const outputFramePreviewReadable = useMemo(
    () => visualizeControlChars(outputFramePreviewRaw),
    [outputFramePreviewRaw]
  );
  const outputFieldDropdownOptions = useMemo(
    () => outputFieldOptions.map((f) => ({ value: f.key, label: f.label })),
    [outputFieldOptions]
  );
  const outputUnitDropdownOptions = useMemo(() => {
    const options: Array<{ value: string; label: string }> = [{ value: 'default', label: 'default' }];
    if (LENGTH_BASE_KEYS.has(outputFieldToAdd)) {
      options.push({ value: 'mm', label: 'mm' }, { value: 'm', label: 'm' });
    }
    if (VOLUME_BASE_KEYS.has(outputFieldToAdd)) {
      options.push({ value: 'm3', label: 'm3' }, { value: 'l', label: 'l' }, { value: 'mm3', label: 'mm3' });
    }
    return options;
  }, [outputFieldToAdd]);
  const outputPrecisionDropdownOptions = useMemo(
    () => Array.from({ length: 9 }, (_, i) => ({ value: String(i), label: `${i}` })),
    []
  );
  useEffect(() => {
    if (!outputUnitDropdownOptions.some((o) => o.value === outputFieldUnit)) {
      setOutputFieldUnit('default');
    }
  }, [outputUnitDropdownOptions, outputFieldUnit]);

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
  }, [analysisApp, yStats, bounds, edgeDiameters]);

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-3xl font-bold text-slate-900">Analytics</h1>
            <p className="text-sm text-gray-500 mt-1">
              Active app: {analysisApp === 'none' ? 'None (acquisition only)' : 'Log measurement'}
            </p>
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

        <div className="grid grid-cols-1 xl:grid-cols-[1.35fr_0.65fr] gap-6">
          <syn-card className="app-card">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-xl font-semibold text-slate-900">Latest 3D Capture</h2>
                <p className="text-xs text-gray-500">Last registered point cloud (unified frame)</p>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
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
                  <syn-checkbox checked={showOriginal} onClick={() => setShowOriginal((v) => !v)}>Original</syn-checkbox>
                  <syn-checkbox checked={colorBySource} onClick={() => setColorBySource((v) => !v)}>Color by source</syn-checkbox>
                  <syn-checkbox checked={fullCloud} onClick={() => setFullCloud((v) => !v)}>Full cloud</syn-checkbox>
                <SynInteropButton
                  size="small"
                  onPress={async () => {
                    await fetchLatest(true);
                    await fetchAnalysis({ forceCloud: true, full: true });
                  }}
                  disabled={loading || status.recording}
                >
                  Load full
                </SynInteropButton>
              </div>
            </div>
            <div className="viewer-panel-height mt-4 relative">
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
                showFloor={false}
              />
              <div className="absolute left-3 top-3 flex max-w-[calc(100%-88px)] items-center gap-2 rounded-md bg-black/40 px-2 py-1 text-xs text-white sm:left-4 sm:top-4">
                <span className="text-gray-200">Color</span>
                <syn-dropdown>
                  <syn-button slot="trigger" size="small" caret="">{colorModeLabel}</syn-button>
                  <syn-menu style={{ minWidth: 160 }}>
                    <syn-menu-item onClick={() => setColorMode('rssi')}>RSSI</syn-menu-item>
                    <syn-menu-item onClick={() => setColorMode('x')}>X</syn-menu-item>
                    <syn-menu-item onClick={() => setColorMode('y')}>Y</syn-menu-item>
                    <syn-menu-item onClick={() => setColorMode('z')}>Z</syn-menu-item>
                  </syn-menu>
                </syn-dropdown>
              </div>
              {analysisTimestampMs && (
                <div className="absolute bottom-4 left-4 rounded-md bg-black/60 px-3 py-2 text-xs text-white">
                  Analysis timestamp: {new Date(analysisTimestampMs).toLocaleString()}
                </div>
              )}
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
            <footer slot="footer">
              <small>Viewer of latest acquisition cloud and overlays.</small>
            </footer>
          </syn-card>

          <div className="space-y-6">
            <syn-card className="app-card">
              <h2 className="text-xl font-semibold text-slate-900">Live Metrics</h2>
              <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
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
              <footer slot="footer">
                <small>Live telemetry from trigger-driven acquisition.</small>
              </footer>
            </syn-card>

            <syn-card className="app-card">
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-slate-900">Output Wizard</h2>
                <div className="flex items-center gap-2">
                  <SynInteropButton className="btn-inline-content" onPress={() => setOutputWizardOpen(true)}>
                    <span className="inline-flex items-center gap-2 whitespace-nowrap">
                      <Wand2 className="h-4 w-4" />
                      Open Builder
                    </span>
                  </SynInteropButton>
                </div>
              </div>
              <p className="text-xs text-gray-500 mt-1">
                Konfiguracja outputu jest dostępna tylko w Builderze.
              </p>
              <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-gray-600 break-all">
                Frame preview: {outputFramePreviewReadable}
              </div>
              {outputSavedAt !== null && (
                <div className="mt-2 text-xs text-emerald-600">
                  Saved {new Date(outputSavedAt).toLocaleTimeString()}
                </div>
              )}
              <footer slot="footer">
                <small>Configure protocol and payload fields for external integration.</small>
              </footer>
            </syn-card>

            <syn-card className="app-card">
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-slate-900">Analysis Results</h2>
                <syn-button
                  onClick={handleSaveAndRecomputeAnalysis}
                  disabled={analysisSaving || status.recording}
                  title={status.recording ? 'Stop acquisition before recompute' : ''}
                >
                  {analysisSaving ? 'Saving...' : 'Save & Recompute'}
                </syn-button>
              </div>
              {analysisApp === 'log' ? (
                <p className="text-xs text-gray-500 mt-1">
                  Fits a circle to each window on the X/Z plane. Length is along Y.
                </p>
              ) : analysisApp === 'none' ? (
                <p className="text-xs text-gray-500 mt-1">
                  Analysis disabled. Acquisition cloud only.
                </p>
              ) : (
                <p className="text-xs text-gray-500 mt-1">
                  Fits conveyor plane and dimensions object points above the plane.
                </p>
              )}

              {analysisApp === 'log' && (
                <div className="mt-4 grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-gray-500">Active app</span>
                    <select className="input" value={analysisApp} onChange={(e) => setAnalysisApp(e.target.value as AnalysisApp)}>
                      <option value="none">None (acquisition only)</option>
                      <option value="log">Log measurement</option>
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
                <div className="mt-4 grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
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
                    <syn-checkbox checked={convDenoiseEnabled} onClick={() => setConvDenoiseEnabled((v) => !v)} />
                    Denoise enabled
                  </label>
                  <label className="flex items-center gap-2 text-xs text-gray-500 mt-6">
                    <syn-checkbox checked={convKeepLargestComponent} onClick={() => setConvKeepLargestComponent((v) => !v)} />
                    Keep largest component
                  </label>
                </div>
              )}

              {analysisSavedAt !== null && (
                <div className="mt-2 text-xs text-emerald-600">
                  Saved {new Date(analysisSavedAt).toLocaleTimeString()}
                </div>
              )}

              <div className="mt-4 flex flex-wrap gap-2">
                {analysisApp === 'log' && (
                  <syn-button variant="filled" onClick={handleAnalyze} disabled={loading || status.recording}>
                    <Wand2 className="h-4 w-4" />
                    Run local analysis
                  </syn-button>
                )}
                <syn-button onClick={() => fetchAnalysis({ forceCloud: true })} disabled={loading}>
                  Fetch server analysis
                </syn-button>
                <syn-button onClick={() => setAnalysis(null)}>
                  Clear
                </syn-button>
              </div>

              {analysisDuration !== null && (
                <div className="mt-3 text-xs text-gray-500">Analysis time: {analysisDuration} ms</div>
              )}

              {logAnalysis && (
                <div className="mt-4 space-y-3 text-sm text-gray-500">
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
              <footer slot="footer">
                <small>Use recompute after settings changes to keep outputs in sync.</small>
              </footer>
            </syn-card>
          </div>
        </div>

        {outputWizardOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/45 backdrop-blur-sm" onClick={() => setOutputWizardOpen(false)} />
            <syn-card className="app-card relative w-[min(920px,96vw)] max-h-[92vh] overflow-auto">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-slate-900">Output Frame Builder</h3>
                <div className="flex items-center gap-2">
                  <SynInteropButton className="btn-inline-content" onPress={handleSaveOutputSettings} disabled={outputSaving}>
                    {outputSaving ? 'Saving...' : 'Save Output'}
                  </SynInteropButton>
                  <syn-button size="small" onClick={() => setOutputWizardOpen(false)} aria-label="Close builder">
                    <X className="h-4 w-4" />
                  </syn-button>
                </div>
              </div>
              <p className="mt-1 text-xs text-gray-500">
                Build custom output frame by adding fields and your own text. Connection is fixed to TCP server on port 2120.
              </p>

              <div className="mt-4 grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
                <label className="flex items-center gap-2 text-xs text-gray-500 mt-6">
                  <syn-checkbox
                    checked={outputSettings.enabled}
                    onClick={() => setOutputSettings((prev) => ({ ...prev, enabled: !prev.enabled }))}
                  >
                    Output enabled
                  </syn-checkbox>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-gray-500">Listen IP</span>
                  <input
                    className="input"
                    value={outputSettings.host}
                    onChange={(e) => setOutputSettings((prev) => ({ ...prev, host: e.target.value }))}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-gray-500">Separator (global)</span>
                  <SynInteropDropdown
                    value={outputSettings.separator}
                    options={OUTPUT_SEPARATOR_OPTIONS}
                    onChange={(next) => setOutputSettings((prev) => ({ ...prev, separator: next }))}
                    className="w-full"
                  />
                </label>
              </div>

              <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-[1.2fr_0.6fr_0.35fr_auto]">
                <div>
                  <div className="text-xs text-gray-500 mb-1">Parameter</div>
                  <SynInteropDropdown
                    value={outputFieldToAdd}
                    options={outputFieldDropdownOptions}
                    onChange={(next) => setOutputFieldToAdd(next)}
                    className="w-full"
                  />
                </div>
                <div>
                  <div className="text-xs text-gray-500 mb-1">Unit formatting</div>
                  <SynInteropDropdown
                    value={outputFieldUnit}
                    options={outputUnitDropdownOptions}
                    onChange={(next) => setOutputFieldUnit(next as OutputUnitChoice)}
                    className="w-full"
                  />
                </div>
                <div>
                  <div className="text-xs text-gray-500 mb-1">Precision</div>
                  <SynInteropDropdown
                    value={outputFieldPrecision}
                    options={outputPrecisionDropdownOptions}
                    onChange={(next) => setOutputFieldPrecision(next)}
                    className="w-full"
                  />
                </div>
                <div className="flex items-end">
                  <syn-button onClick={addFieldToFrame}>
                    <Plus className="h-4 w-4" />
                    Add field
                  </syn-button>
                </div>
              </div>

              <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-[1fr_auto]">
                <div>
                  <div className="text-xs text-gray-500 mb-1">Custom text</div>
                  <input
                    className="input"
                    value={outputCustomText}
                    placeholder="np. MACHINE=LINE_A"
                    onChange={(e) => setOutputCustomText(e.target.value)}
                  />
                </div>
                <div className="flex items-end">
                  <syn-button onClick={addTextToFrame}>
                    <Plus className="h-4 w-4" />
                    Add text
                  </syn-button>
                </div>
              </div>

              <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-[1fr_1fr_auto_auto]">
                <div>
                  <div className="text-xs text-gray-500 mb-1">Special marker (preset)</div>
                  <SynInteropDropdown
                    value={outputMarkerPreset}
                    options={OUTPUT_MARKER_OPTIONS}
                    onChange={(next) => setOutputMarkerPreset(next)}
                    className="w-full"
                  />
                </div>
                <div>
                  <div className="text-xs text-gray-500 mb-1">Custom marker</div>
                  <input
                    className="input"
                    value={outputMarkerCustom}
                    placeholder="np. # lub @@"
                    onChange={(e) => setOutputMarkerCustom(e.target.value)}
                  />
                </div>
                <div className="flex items-end">
                  <syn-button onClick={() => addMarkerToFrame(outputMarkerPreset, OUTPUT_MARKER_OPTIONS.find((m) => m.value === outputMarkerPreset)?.label || '')}>
                    Add preset marker
                  </syn-button>
                </div>
                <div className="flex items-end">
                  <syn-button onClick={() => { if (outputMarkerCustom.length > 0) { addMarkerToFrame(outputMarkerCustom, 'Custom marker'); setOutputMarkerCustom(''); } }}>
                    <Plus className="h-4 w-4" />
                    Add custom marker
                  </syn-button>
                </div>
              </div>

              <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3">
                <div className="text-xs font-semibold text-slate-700">Frame items order</div>
                <div className="mt-2 space-y-2">
                  {(outputSettings.output_frame_items || []).map((item, idx) => (
                    <div key={`frame-item-${idx}`} className="flex items-center justify-between rounded border border-slate-200 bg-white px-2 py-1.5 text-xs">
                      <div className="min-w-0 truncate">
                        {humanizeFrameItem(item)}
                        {item.type === 'field' && (
                          <span className="ml-2 text-[10px] text-slate-500">p:{typeof item.precision === 'number' ? item.precision : 2}</span>
                        )}
                      </div>
                      <div className="flex items-center gap-1">
                        {item.type === 'field' && (
                          <div className="w-20">
                            <SynInteropDropdown
                              value={String(typeof item.precision === 'number' ? item.precision : 2)}
                              options={outputPrecisionDropdownOptions}
                              onChange={(next) => setFrameItemPrecision(idx, Number(next))}
                            />
                          </div>
                        )}
                        <syn-button size="small" onClick={() => moveFrameItem(idx, -1)} disabled={idx === 0}>
                          <ArrowUp className="h-3.5 w-3.5" />
                        </syn-button>
                        <syn-button size="small" onClick={() => moveFrameItem(idx, 1)} disabled={idx === (outputSettings.output_frame_items || []).length - 1}>
                          <ArrowDown className="h-3.5 w-3.5" />
                        </syn-button>
                        <syn-button variant="danger" size="small" onClick={() => removeFrameItem(idx)}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </syn-button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
                <div className="text-xs font-semibold text-slate-700 mb-1">Frame preview (readable)</div>
                <div className="rounded border border-slate-200 bg-white p-3 text-sm font-mono text-slate-800 break-all min-h-[56px]">
                  {outputFramePreviewReadable}
                </div>
                <div className="mt-2 text-[11px] text-slate-500">Raw</div>
                <div className="rounded border border-slate-200 bg-white p-2 text-xs font-mono text-slate-700 break-all min-h-[40px]">
                  {outputFramePreviewRaw}
                </div>
              </div>

              <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
                <div className="text-xs font-semibold text-slate-700 mb-1">Last data example from system</div>
                <div className="rounded border border-slate-200 bg-white p-3 text-sm font-mono text-slate-800 break-all min-h-[56px]">
                  {outputServerPreview
                    ? visualizeControlChars(
                        typeof outputServerPreview.payload === 'string'
                          ? outputServerPreview.payload
                          : JSON.stringify(outputServerPreview.payload)
                      )
                    : '(no preview data)'}
                </div>
                {outputServerPreview && (
                  <div className="mt-1 text-[11px] text-slate-500">
                    source: {outputServerPreview.source} | app: {outputServerPreview.analysis_app} | ts:{' '}
                    {outputServerPreview.timestamp_ms ? new Date(outputServerPreview.timestamp_ms).toLocaleString() : '-'}
                  </div>
                )}
              </div>

              <footer slot="footer">
                <small>Use Save Output to persist this frame and apply it to TCP output.</small>
              </footer>
            </syn-card>
          </div>
        )}
      </div>
    </Layout>
  );
}
