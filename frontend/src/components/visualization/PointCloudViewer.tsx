/**
 * 3D Point Cloud Visualization
 */

'use client';

import React, { useMemo } from 'react';
import dynamic from 'next/dynamic';
import { Download, RefreshCw } from 'lucide-react';
import type { PointCloudStatistics } from '@/types';

// Dynamic import Plotly to avoid SSR issues
const Plot = dynamic(() => import('react-plotly.js'), { ssr: false });

interface PointCloudViewerProps {
  statistics?: PointCloudStatistics;
  loading?: boolean;
  onRefresh?: () => void;
}

export default function PointCloudViewer({
  statistics,
  loading = false,
  onRefresh,
}: PointCloudViewerProps) {
  const plotData = useMemo(() => {
    if (!statistics || !statistics.num_points) {
      return [];
    }

    // Dummy data - będzie zmieniane na rzeczywiste dane z backendU
    const pointCount = statistics.num_points;
    const xMin = statistics.bounds.min.x;
    const yMin = statistics.bounds.min.y;
    const zMin = statistics.bounds.min.z;
    const xSize = statistics.bounds.size.x;
    const ySize = statistics.bounds.size.y;
    const zSize = statistics.bounds.size.z;

    // Generate sample points within bounds
    const sampleSize = Math.min(pointCount, 5000); // Limit for performance
    const xs = Array.from({ length: sampleSize }, () => xMin + Math.random() * xSize);
    const ys = Array.from({ length: sampleSize }, () => yMin + Math.random() * ySize);
    const zs = Array.from({ length: sampleSize }, () => zMin + Math.random() * zSize);

    return [
      {
        x: xs,
        y: ys,
        z: zs,
        mode: 'markers',
        type: 'scatter3d',
        marker: {
          size: 3,
          color: zs,
          colorscale: 'Viridis',
          showscale: true,
          colorbar: { title: 'Z (mm)' },
        },
        name: 'Point Cloud',
      },
    ];
  }, [statistics]);

  const layout = {
    autosize: true,
    scene: {
      xaxis: { title: 'X (mm)' },
      yaxis: { title: 'Y (mm)' },
      zaxis: { title: 'Z (mm)' },
    },
    margin: { l: 0, r: 0, b: 0, t: 0 },
  };

  return (
    <div className="card h-full">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-slate-900">3D Point Cloud</h3>
        <div className="flex gap-2">
          <button
            className="btn-secondary px-3 py-2"
            onClick={onRefresh}
            disabled={loading}
            aria-label="Refresh"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
          <button className="btn-secondary px-3 py-2" aria-label="Download">
            <Download className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div style={{ height: 'calc(100% - 40px)', minHeight: 'clamp(260px, 45vh, 400px)' }}>
        {loading ? (
          <div className="h-full w-full rounded-lg bg-gray-100 shimmer" />
        ) : statistics ? (
          <Plot
            data={plotData as any}
            layout={layout as any}
            style={{ width: '100%', height: '100%' }}
            config={{
              responsive: true,
              displayModeBar: true,
              displaylogo: false,
            }}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-gray-500">
            No data to display
          </div>
        )}
      </div>
    </div>
  );
}
