/**
 * Main Dashboard Page
 */

'use client';

import React, { useEffect } from 'react';
import { Toaster } from 'react-hot-toast';
import Layout from '@/components/layout/Layout';
import PointCloudViewer from '@/components/visualization/PointCloudViewer';
import AcquisitionControl from '@/components/controls/AcquisitionControl';
import StatisticsPanel from '@/components/controls/StatisticsPanel';
import { usePicoscan } from '@/hooks/usePicoscan';

export default function Dashboard() {
  const {
    devices,
    listening,
    loading,
    error,
    pointCloudData,
    loadDevices,
    startListening,
    stopListening,
    receiveData,
  } = usePicoscan();

  useEffect(() => {
    loadDevices();
  }, [loadDevices]);

  return (
    <Layout>
      <Toaster />
      
      {error && (
        <div className="mb-4 rounded-xl bg-gradient-to-r from-rose-50 to-red-50 border border-rose-200 p-4 text-rose-900">
          {error}
        </div>
      )}

      <div className="grid gap-6 grid-cols-1 lg:grid-cols-3">
        {/* Main Viewer */}
        <div className="lg:col-span-2">
          <PointCloudViewer
            statistics={pointCloudData?.statistics}
            loading={loading}
            onRefresh={() => receiveData(devices.map((d) => d.device_id))}
          />
        </div>

        {/* Right Panel - Controls & Stats */}
        <div className="flex flex-col gap-6">
          <AcquisitionControl
            devices={devices}
            listening={listening}
            loading={loading}
            onStartListening={startListening}
            onStopListening={stopListening}
            onReceiveData={receiveData}
          />

          <StatisticsPanel statistics={pointCloudData?.statistics} loading={loading} />
        </div>
      </div>
    </Layout>
  );
}
