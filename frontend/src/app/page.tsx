'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import Layout from '@/components/layout/Layout';
import api from '@/services/api';

interface TriggerStatus {
  recording: boolean;
  distance_mm: number;
  speed_mps: number | null;
  encoder_speed_mps?: number | null;
  profiling_distance_mm: number | null;
  profiles_count?: number;
  last_update_ts: number | null;
  trigger_source?: string;
}

interface AnalysisSettings {
  active_app: 'none' | 'log' | 'conveyor_object' | string;
}

interface MotionSettings {
  mode: 'fixed' | 'encoder' | string;
  fixed_speed_mps?: number | null;
  profiling_distance_mm?: number | null;
}

interface DeviceCfg {
  device_id: string;
  name?: string;
  enabled?: boolean;
}

interface DeviceHealth {
  availability?: 'online' | 'offline' | 'unknown' | string;
  latest_data_age_s?: number | null;
  data_rate_hz?: number | null;
  incomplete_frames_dropped?: number;
}

interface AvailabilityResponse {
  enabled_total: number;
  online_ids: string[];
  offline_ids: string[];
  unknown_ids: string[];
  health: Record<string, DeviceHealth>;
}

interface AnalyticsResultsResponse {
  analysis_app?: 'none' | 'log' | 'conveyor_object' | string;
  metrics?: Record<string, unknown> | null;
  analysis_timestamp_ms?: number | null;
}

interface SystemMetrics {
  available: boolean;
  os?: {
    name?: string;
    release?: string;
    version?: string;
    machine?: string;
  };
  cpu?: {
    percent?: number;
    cores_logical?: number | null;
    cores_physical?: number | null;
  };
  memory?: {
    total_bytes?: number;
    available_bytes?: number;
    used_bytes?: number;
    percent?: number;
  };
  disk?: {
    path?: string;
    total_bytes?: number;
    used_bytes?: number;
    free_bytes?: number;
    percent?: number;
  };
  uptime_s?: number;
  process?: {
    pid?: number;
    rss_bytes?: number;
    cpu_percent?: number;
  };
}

function formatBytes(value?: number): string {
  if (!value || value <= 0) return '-';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = value;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
}

function formatAppName(app: string | undefined): string {
  if (app === 'none') return 'None (acquisition only)';
  return app === 'conveyor_object' ? 'Conveyor Object' : 'Log Measurement';
}

function availabilityBadgeClass(status: string): string {
  if (status === 'online') return 'bg-emerald-100 text-emerald-800 border-emerald-200';
  if (status === 'offline') return 'bg-rose-100 text-rose-800 border-rose-200';
  return 'bg-amber-100 text-amber-800 border-amber-200';
}

export default function DashboardPage() {
  const [loading, setLoading] = useState(false);
  const [lastRefreshTs, setLastRefreshTs] = useState<number | null>(null);
  const [status, setStatus] = useState<TriggerStatus | null>(null);
  const [analysisSettings, setAnalysisSettings] = useState<AnalysisSettings | null>(null);
  const [motionSettings, setMotionSettings] = useState<MotionSettings | null>(null);
  const [analyticsResults, setAnalyticsResults] = useState<AnalyticsResultsResponse | null>(null);
  const [devices, setDevices] = useState<DeviceCfg[]>([]);
  const [availability, setAvailability] = useState<AvailabilityResponse | null>(null);
  const [systemMetrics, setSystemMetrics] = useState<SystemMetrics | null>(null);

  const refreshAll = async () => {
    setLoading(true);
    try {
      const [st, anCfg, moCfg, anRes, devs, avail, sys] = await Promise.all([
        api.get('/acquisition/trigger/status'),
        api.get('/calibration/analysis-settings'),
        api.get('/calibration/motion-settings'),
        api.get('/acquisition/analytics/results'),
        api.get('/devices/'),
        api.get('/acquisition/devices/availability'),
        api.get('/system/metrics'),
      ]);
      setStatus(st.data || null);
      setAnalysisSettings(anCfg.data || null);
      setMotionSettings(moCfg.data || null);
      setAnalyticsResults(anRes.data || null);
      setDevices(Array.isArray(devs.data) ? devs.data : []);
      setAvailability(avail.data || null);
      setSystemMetrics(sys.data || null);
      setLastRefreshTs(Date.now());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refreshAll();
    const id = setInterval(() => {
      void refreshAll();
    }, 3000);
    return () => clearInterval(id);
  }, []);

  const liveSpeedMps = status?.encoder_speed_mps ?? status?.speed_mps ?? null;
  const distanceM = ((status?.distance_mm || 0) / 1000.0);
  const activeApp = String(analysisSettings?.active_app || analyticsResults?.analysis_app || 'log');
  const workMode = String(motionSettings?.mode || 'fixed');

  const logMetrics = useMemo(() => {
    if (activeApp !== 'log') return null;
    return (analyticsResults?.metrics || null) as Record<string, unknown> | null;
  }, [activeApp, analyticsResults?.metrics]);

  const conveyorMetrics = useMemo(() => {
    if (activeApp !== 'conveyor_object') return null;
    return (analyticsResults?.metrics || null) as Record<string, unknown> | null;
  }, [activeApp, analyticsResults?.metrics]);

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-3xl font-bold text-slate-900">Dashboard</h1>
            <p className="text-sm text-gray-500 mt-1">
              Najważniejsze informacje operacyjne systemu.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <syn-button onClick={() => void refreshAll()} disabled={loading}>
              <RefreshCw className="h-4 w-4" />
              Refresh
            </syn-button>
            <div className="text-xs text-gray-500">
              {lastRefreshTs ? `Updated ${new Date(lastRefreshTs).toLocaleTimeString()}` : 'Loading...'}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          <syn-card className="app-card">
            <div className="text-xs text-gray-500">Active analysis app</div>
            <div className="mt-1 text-xl font-semibold text-slate-900">{formatAppName(activeApp)}</div>
            <div className="mt-2 text-xs text-gray-500">Selection from System Config.</div>
          </syn-card>

          <syn-card className="app-card">
            <div className="text-xs text-gray-500">Work mode</div>
            <div className="mt-1 text-xl font-semibold text-slate-900">
              {workMode === 'encoder' ? 'Encoder mode' : 'Fixed speed mode'}
            </div>
            <div className="mt-2 text-xs text-gray-500">
              {workMode === 'encoder'
                ? 'Speed is measured from encoder.'
                : `Configured speed: ${typeof motionSettings?.fixed_speed_mps === 'number' ? `${motionSettings.fixed_speed_mps.toFixed(2)} m/s` : '-'}`}
            </div>
          </syn-card>

          <syn-card className="app-card">
            <div className="text-xs text-gray-500">Current speed</div>
            <div className="mt-1 text-xl font-semibold text-slate-900">
              {typeof liveSpeedMps === 'number' ? `${liveSpeedMps.toFixed(2)} m/s` : '-'}
            </div>
            <div className="mt-2 text-xs text-gray-500">
              Recording: <span className="font-semibold">{status?.recording ? 'ON' : 'OFF'}</span>
            </div>
          </syn-card>

          <syn-card className="app-card">
            <div className="text-xs text-gray-500">Distance / profiles</div>
            <div className="mt-1 text-xl font-semibold text-slate-900">{distanceM.toFixed(3)} m</div>
            <div className="mt-2 text-xs text-gray-500">
              Profiles: {status?.profiles_count ?? 0} | Profiling distance: {status?.profiling_distance_mm ?? '-'} mm
            </div>
          </syn-card>

          <syn-card className="app-card">
            <div className="text-xs text-gray-500">System health</div>
            <div className="mt-1 text-xl font-semibold text-slate-900">
              {systemMetrics?.available ? `${systemMetrics.cpu?.percent?.toFixed(0) ?? '-'}% CPU` : 'Unavailable'}
            </div>
            <div className="mt-2 text-xs text-gray-500">
              RAM: {typeof systemMetrics?.memory?.percent === 'number' ? `${systemMetrics.memory.percent.toFixed(0)}%` : '-'} |
              Disk: {typeof systemMetrics?.disk?.percent === 'number' ? ` ${systemMetrics.disk.percent.toFixed(0)}%` : ' -'}
            </div>
            <div className="mt-1 text-[11px] text-gray-500">
              {systemMetrics?.os?.name ?? '-'} {systemMetrics?.os?.release ?? ''}
            </div>
          </syn-card>
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_1fr]">
          <syn-card className="app-card">
            <h2 className="text-xl font-semibold text-slate-900">Latest Analysis Result</h2>
            <p className="text-xs text-gray-500 mt-1">
              Adekwatnie do wybranej aplikacji: {formatAppName(activeApp)}.
            </p>

            {activeApp === 'log' ? (
              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 text-sm">
                <div>
                  <div className="text-xs text-gray-500">Volume</div>
                  <div className="text-lg font-semibold text-slate-900">
                    {typeof logMetrics?.volume_m3 === 'number' ? `${(logMetrics.volume_m3 as number).toFixed(6)} m3` : '-'}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-gray-500">Length</div>
                  <div className="text-lg font-semibold text-slate-900">
                    {typeof logMetrics?.total_length_mm === 'number' ? `${((logMetrics.total_length_mm as number) / 1000).toFixed(3)} m` : '-'}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-gray-500">Diameter avg</div>
                  <div className="text-lg font-semibold text-slate-900">
                    {typeof (logMetrics?.diameter_mm as Record<string, unknown> | undefined)?.avg === 'number'
                      ? `${((logMetrics?.diameter_mm as Record<string, unknown>).avg as number).toFixed(1)} mm`
                      : '-'}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-gray-500">Slices</div>
                  <div className="text-lg font-semibold text-slate-900">
                    {typeof logMetrics?.total_slices === 'number' ? String(logMetrics.total_slices) : '-'}
                  </div>
                </div>
              </div>
            ) : (
              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 text-sm">
                <div>
                  <div className="text-xs text-gray-500">Object points</div>
                  <div className="text-lg font-semibold text-slate-900">
                    {typeof (conveyorMetrics?.object as Record<string, unknown> | undefined)?.points_count === 'number'
                      ? String((conveyorMetrics?.object as Record<string, unknown>).points_count)
                      : '-'}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-gray-500">BBox volume</div>
                  <div className="text-lg font-semibold text-slate-900">
                    {typeof (conveyorMetrics?.object as Record<string, unknown> | undefined)?.bbox_volume_m3 === 'number'
                      ? `${((conveyorMetrics?.object as Record<string, unknown>).bbox_volume_m3 as number).toFixed(6)} m3`
                      : '-'}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-gray-500">BBox L x W x H</div>
                  <div className="text-lg font-semibold text-slate-900">
                    {(() => {
                      const bbox = ((conveyorMetrics?.object as Record<string, unknown> | undefined)?.bbox_mm as Record<string, unknown> | undefined);
                      if (!bbox) return '-';
                      const l = typeof bbox.length === 'number' ? (bbox.length as number).toFixed(1) : '-';
                      const w = typeof bbox.width === 'number' ? (bbox.width as number).toFixed(1) : '-';
                      const h = typeof bbox.height === 'number' ? (bbox.height as number).toFixed(1) : '-';
                      return `${l} x ${w} x ${h} mm`;
                    })()}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-gray-500">Plane RMSE</div>
                  <div className="text-lg font-semibold text-slate-900">
                    {typeof (conveyorMetrics?.plane as Record<string, unknown> | undefined)?.rmse_mm === 'number'
                      ? `${((conveyorMetrics?.plane as Record<string, unknown>).rmse_mm as number).toFixed(2)} mm`
                      : '-'}
                  </div>
                </div>
              </div>
            )}

            <div className="mt-3 text-xs text-gray-500">
              Last analysis: {analyticsResults?.analysis_timestamp_ms ? new Date(analyticsResults.analysis_timestamp_ms).toLocaleString() : '-'}
            </div>
          </syn-card>

          <syn-card className="app-card">
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-semibold text-slate-900">Scanner Status</h2>
              <div className="text-xs text-gray-500">
                online {availability?.online_ids?.length ?? 0} / enabled {availability?.enabled_total ?? 0}
              </div>
            </div>
            <p className="text-xs text-gray-500 mt-1">Stan wszystkich dodanych skanerów.</p>

            <div className="mt-4 space-y-2">
              {devices.length === 0 && (
                <div className="text-sm text-gray-500">No devices configured.</div>
              )}

              {devices.map((d) => {
                const h = availability?.health?.[d.device_id];
                const st = String(h?.availability || 'unknown');
                return (
                  <div key={d.device_id} className="rounded-md border border-slate-200 px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="font-medium text-slate-900 truncate">{d.name || d.device_id}</div>
                        <div className="text-xs text-gray-500 truncate">{d.device_id}</div>
                      </div>
                      <span className={`text-[11px] px-2 py-0.5 rounded border ${availabilityBadgeClass(st)}`}>
                        {st.toUpperCase()}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-gray-500">
                      data age: {typeof h?.latest_data_age_s === 'number' ? `${h.latest_data_age_s.toFixed(2)} s` : '-'} | rate:{' '}
                      {typeof h?.data_rate_hz === 'number' ? `${h.data_rate_hz.toFixed(1)} Hz` : '-'} | dropped:{' '}
                      {h?.incomplete_frames_dropped ?? 0}
                    </div>
                  </div>
                );
              })}
            </div>
          </syn-card>
        </div>
      </div>
    </Layout>
  );
}
