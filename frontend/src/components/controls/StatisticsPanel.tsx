/**
 * Statistics Panel
 */

'use client';

import React from 'react';
import type { PointCloudStatistics } from '@/types';
import { formatNumber } from '@/utils/helpers';

interface StatisticsPanelProps {
  statistics?: PointCloudStatistics;
  loading?: boolean;
}

export default function StatisticsPanel({ statistics, loading }: StatisticsPanelProps) {
  if (!statistics) {
    return (
      <div className="card text-center text-gray-500">No statistics available</div>
    );
  }

  const stats = [
    { label: 'Points', value: statistics.num_points.toLocaleString() },
    { label: 'Centroid X', value: `${formatNumber(statistics.centroid.x)} mm` },
    { label: 'Centroid Y', value: `${formatNumber(statistics.centroid.y)} mm` },
    { label: 'Centroid Z', value: `${formatNumber(statistics.centroid.z)} mm` },
    { label: 'Size X', value: `${formatNumber(statistics.bounds.size.x)} mm` },
    { label: 'Size Y', value: `${formatNumber(statistics.bounds.size.y)} mm` },
    { label: 'Size Z', value: `${formatNumber(statistics.bounds.size.z)} mm` },
    { label: 'Density', value: statistics.density ? formatNumber(statistics.density) : 'N/A' },
  ];

  return (
    <div className="card">
      <h3 className="text-lg font-semibold text-slate-900">Statistics</h3>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {stats.map((stat) => (
          <div key={stat.label} className="flex flex-col gap-1">
            <span className="text-xs font-medium text-gray-600">
              {stat.label}
            </span>
            <span className="text-sm font-semibold text-slate-900">
              {stat.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
