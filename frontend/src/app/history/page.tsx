'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import Layout from '@/components/layout/Layout';
import api from '@/services/api';
import PointCloudThreeViewer, { type PointCloudThreeViewerHandle } from '@/components/visualization/PointCloudThreeViewer';

interface HistoryItem {
  id: string;
  created_at: number;
  profiles_count?: number;
  distance_mm?: number;
  points_count?: number;
  augmented_points_count?: number;
  metrics?: any;
}

export default function HistoryPage() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [selected, setSelected] = useState<HistoryItem | null>(null);
  const [details, setDetails] = useState<any>(null);
  const [hoverPoint, setHoverPoint] = useState<number[] | null>(null);
  const [showOriginal, setShowOriginal] = useState(true);
  const [showAugmented, setShowAugmented] = useState(true);
  const [colorBySource, setColorBySource] = useState(true);
  const [viewerKey, setViewerKey] = useState(0);
  const viewerRef = useRef<PointCloudThreeViewerHandle | null>(null);
  const fitOnceRef = useRef(false);

  const fetchList = async () => {
    const res = await api.get('/acquisition/history');
    setItems(res.data || []);
    if (!selected && res.data && res.data.length > 0) {
      setSelected(res.data[0]);
    }
  };

  const fetchDetails = async (id: string) => {
    const res = await api.get(`/acquisition/history/${id}`);
    setDetails(res.data);
    setViewerKey((v) => v + 1);
    fitOnceRef.current = false;
  };

  useEffect(() => {
    fetchList();
  }, []);

  useEffect(() => {
    if (selected?.id) {
      fetchDetails(selected.id);
    }
  }, [selected?.id]);

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

  const metrics = details?.metrics;

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-slate-900">History</h1>
            <p className="text-sm text-gray-500 mt-1">Archived measurements (last 10)</p>
          </div>
          <button className="btn-secondary" onClick={fetchList}>
            Refresh
          </button>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[0.5fr_1.5fr] gap-6">
          <div className="card">
            <h2 className="text-xl font-semibold text-slate-900">Measurements</h2>
            <div className="mt-3 space-y-2 max-h-[520px] overflow-auto">
              {items.length === 0 && (
                <div className="text-sm text-gray-500">No history entries yet.</div>
              )}
              {items.map((item) => (
                <button
                  key={item.id}
                  className={`w-full text-left rounded-md border px-3 py-2 text-sm ${
                    selected?.id === item.id
                      ? 'border-blue-500 bg-blue-50 text-blue-900'
                      : 'border-slate-200 hover:border-slate-300'
                  }`}
                  onClick={() => setSelected(item)}
                >
                  <div className="font-semibold">#{item.id}</div>
                  <div className="text-xs text-gray-500">
                    {item.created_at ? new Date(item.created_at).toLocaleString() : ''}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">
                    points: {item.points_count ?? 0} • augmented: {item.augmented_points_count ?? 0}
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-6">
            <div className="card">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-xl font-semibold text-slate-900">Preview</h2>
                  <p className="text-xs text-gray-500">Original vs augmented cloud</p>
                </div>
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={showOriginal}
                      onChange={(e) => setShowOriginal(e.target.checked)}
                    />
                    Original
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={showAugmented}
                      onChange={(e) => setShowAugmented(e.target.checked)}
                    />
                    Augmented
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={colorBySource}
                      onChange={(e) => setColorBySource(e.target.checked)}
                    />
                    Color by source
                  </label>
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
                  colorScaleMode={colorBySource ? 'rssi100' : 'auto'}
                  onHoverPoint={setHoverPoint}
                  showOriginAxes
                  originAxisSize={1000}
                />
                {hoverPoint && (
                  <div className="absolute bottom-4 right-4 rounded-md bg-black/70 px-3 py-2 text-xs text-white">
                    <div className="font-semibold mb-1">Point data</div>
                    <div>x: {hoverPoint[0]?.toFixed(2)}</div>
                    <div>y: {hoverPoint[1]?.toFixed(2)}</div>
                    <div>z: {hoverPoint[2]?.toFixed(2)}</div>
                    <div>src: {hoverPoint.length >= 4 ? (hoverPoint[3] > 50 ? 'augmented' : 'original') : 'n/a'}</div>
                  </div>
                )}
              </div>
            </div>

            <div className="card">
              <h2 className="text-xl font-semibold text-slate-900">Results</h2>
              {!metrics && <div className="text-sm text-gray-500">No metrics available.</div>}
              {metrics && (
                <div className="mt-3 grid grid-cols-2 gap-3 text-sm text-gray-500">
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
                    <div className="text-lg font-semibold text-slate-900">
                      {metrics.diameter_mm.min.toFixed(1)} / {metrics.diameter_mm.max.toFixed(1)} mm
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-gray-400">Volume</div>
                    <div className="text-lg font-semibold text-slate-900">{metrics.volume_m3.toFixed(6)} m³</div>
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
