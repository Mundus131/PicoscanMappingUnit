'use client';
/* eslint-disable react-hooks/set-state-in-effect */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Layout from '@/components/layout/Layout';
import api from '@/services/api';
import PointCloudThreeViewer, { type PointCloudThreeViewerHandle } from '@/components/visualization/PointCloudThreeViewer';
import { SynInteropCheckbox } from '@/components/synergy/SynInterop';

interface HistoryItem {
  id: string;
  created_at: number;
  profiles_count?: number;
  distance_mm?: number;
  points_count?: number;
  augmented_points_count?: number;
  metrics?: unknown;
}

export default function HistoryPage() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [selected, setSelected] = useState<HistoryItem | null>(null);
  const [details, setDetails] = useState<{
    original_points?: number[][];
    augmented_points?: number[][];
    metrics?: unknown;
  } | null>(null);
  const [hoverPoint, setHoverPoint] = useState<number[] | null>(null);
  const [showOriginal, setShowOriginal] = useState(true);
  const [showAugmented, setShowAugmented] = useState(true);
  const [colorBySource, setColorBySource] = useState(true);
  const [colorMode, setColorMode] = useState<'rssi' | 'x' | 'y' | 'z'>('rssi');
  const [viewerKey, setViewerKey] = useState(0);
  const viewerRef = useRef<PointCloudThreeViewerHandle | null>(null);
  const fitOnceRef = useRef(false);

  const fetchList = useCallback(async () => {
    const res = await api.get('/acquisition/history');
    const list = res.data || [];
    setItems(list);
    setSelected((prev) => prev ?? (list.length > 0 ? list[0] : null));
  }, []);

  const fetchDetails = useCallback(async (id: string) => {
    const res = await api.get(`/acquisition/history/${id}`);
    setDetails(res.data);
    setViewerKey((v) => v + 1);
    fitOnceRef.current = false;
  }, []);

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  useEffect(() => {
    if (selected?.id) {
      fetchDetails(selected.id);
    }
  }, [selected?.id, fetchDetails]);

  useEffect(() => {
    if (!details || !details.original_points) return;
    if (!fitOnceRef.current) {
      fitOnceRef.current = true;
      setTimeout(() => {
        viewerRef.current?.resetView();
        viewerRef.current?.fitToPoints();
      }, 150);
    }
  }, [details, viewerKey]);

  const displayPoints = useMemo(() => {
    const originals = showOriginal ? (details?.original_points || []) : [];
    const augmented = showAugmented ? (details?.augmented_points || []) : [];
    if (colorBySource) {
      const origTagged = originals.map((p: number[]) => [p[0], p[1], p[2], 20]);
      const augTagged = augmented.map((p: number[]) => [p[0], p[1], p[2], 90]);
      return [...origTagged, ...augTagged];
    }
    return [...originals, ...augmented];
  }, [details, showOriginal, showAugmented, colorBySource]);

  const metrics = details?.metrics as {
    total_slices: number;
    total_length_mm: number;
    diameter_mm: { avg: number; min: number; max: number };
    volume_m3: number;
  } | undefined;
  const colorModeLabel = colorMode.toUpperCase();

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-3xl font-bold text-slate-900">History</h1>
            <p className="mt-1 text-sm text-gray-500">Archived measurements (last 10)</p>
          </div>
          <syn-button onClick={fetchList}>Refresh</syn-button>
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[0.5fr_1.5fr]">
          <syn-card className="app-card">
            <h2 className="text-xl font-semibold text-slate-900">Measurements</h2>
            <div className="mt-3 max-h-[520px] space-y-2 overflow-auto">
              {items.length === 0 && <div className="text-sm text-gray-500">No history entries yet.</div>}
              {items.map((item) => (
                <button
                  key={item.id}
                  className={`w-full rounded-md border px-3 py-2 text-left text-sm ${
                    selected?.id === item.id ? 'border-blue-500 bg-blue-50 text-blue-900' : 'border-slate-200 hover:border-slate-300'
                  }`}
                  onClick={() => setSelected(item)}
                >
                  <div className="font-semibold">#{item.id}</div>
                  <div className="text-xs text-gray-500">{item.created_at ? new Date(item.created_at).toLocaleString() : ''}</div>
                  <div className="mt-1 text-xs text-gray-500">
                    points: {item.points_count ?? 0} | augmented: {item.augmented_points_count ?? 0}
                  </div>
                </button>
              ))}
            </div>
            <footer slot="footer">
              <small>Choose capture to inspect point cloud and computed metrics.</small>
            </footer>
          </syn-card>

          <div className="space-y-6">
            <syn-card className="app-card">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h2 className="text-xl font-semibold text-slate-900">Preview</h2>
                  <p className="text-xs text-gray-500">Original vs augmented cloud</p>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <SynInteropCheckbox checked={showOriginal} onToggle={setShowOriginal}>Original</SynInteropCheckbox>
                  <SynInteropCheckbox checked={showAugmented} onToggle={setShowAugmented}>Augmented</SynInteropCheckbox>
                  <SynInteropCheckbox checked={colorBySource} onToggle={setColorBySource}>Color by source</SynInteropCheckbox>
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
                  gridStep={1000}
                  colorScaleMode={colorBySource ? 'rssi100' : 'auto'}
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
                {hoverPoint && (
                  <div className="absolute bottom-4 right-4 rounded-md bg-black/70 px-3 py-2 text-xs text-white">
                    <div className="mb-1 font-semibold">Point data</div>
                    <div>x: {hoverPoint[0]?.toFixed(2)}</div>
                    <div>y: {hoverPoint[1]?.toFixed(2)}</div>
                    <div>z: {hoverPoint[2]?.toFixed(2)}</div>
                    <div>src: {hoverPoint.length >= 4 ? (hoverPoint[3] > 50 ? 'augmented' : 'original') : 'n/a'}</div>
                  </div>
                )}
              </div>
            </syn-card>

            <syn-card className="app-card">
              <h2 className="text-xl font-semibold text-slate-900">Results</h2>
              {!metrics && <div className="text-sm text-gray-500">No metrics available.</div>}
              {metrics && (
                <div className="mt-3 grid grid-cols-1 gap-3 text-sm text-gray-500 sm:grid-cols-2">
                  <div>
                    <div className="text-xs text-gray-400">Slices</div>
                    <div className="text-lg font-semibold text-slate-900">{metrics.total_slices}</div>
                  </div>
                  <div>
                    <div className="text-xs text-gray-400">Length</div>
                    <div className="text-lg font-semibold text-slate-900">{(metrics.total_length_mm / 1000).toFixed(2)} m</div>
                  </div>
                  <div>
                    <div className="text-xs text-gray-400">Diameter avg</div>
                    <div className="text-lg font-semibold text-slate-900">{metrics.diameter_mm.avg.toFixed(1)} mm</div>
                  </div>
                  <div>
                    <div className="text-xs text-gray-400">Diameter min / max</div>
                    <div className="text-lg font-semibold text-slate-900">{metrics.diameter_mm.min.toFixed(1)} / {metrics.diameter_mm.max.toFixed(1)} mm</div>
                  </div>
                  <div>
                    <div className="text-xs text-gray-400">Volume</div>
                    <div className="text-lg font-semibold text-slate-900">{metrics.volume_m3.toFixed(6)} m3</div>
                  </div>
                </div>
              )}
            </syn-card>
          </div>
        </div>
      </div>
    </Layout>
  );
}
