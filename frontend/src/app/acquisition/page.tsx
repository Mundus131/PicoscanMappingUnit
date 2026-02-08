/**
 * Acquisition Page
 */

'use client';

import React, { useEffect } from 'react';
import Layout from '@/components/layout/Layout';
import { Play, Square, RefreshCw } from 'lucide-react';
import { usePicoscan } from '@/hooks/usePicoscan';
import toast from 'react-hot-toast';
import { Toaster } from 'react-hot-toast';

export default function AcquisitionPage() {
  const {
    devices,
    listening,
    loading,
    pointCloudData,
    loadDevices,
    startListening,
    stopListening,
    receiveData,
  } = usePicoscan();

  const [selectedDevice, setSelectedDevice] = React.useState<string>('');
  const [numSegments, setNumSegments] = React.useState('10');

  useEffect(() => {
    loadDevices();
  }, [loadDevices]);

  useEffect(() => {
    if (devices.length > 0 && !selectedDevice) {
      setSelectedDevice(devices[0].device_id);
    }
  }, [devices, selectedDevice]);

  const handleReceive = async () => {
    if (!selectedDevice) {
      toast.error('Select a device');
      return;
    }
    try {
      await receiveData([selectedDevice]);
      toast.success('Data received');
    } catch (error) {
      toast.error('Failed to receive data');
    }
  };

  return (
    <Layout>
      <Toaster />
      
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-slate-900">Acquisition Control</h1>
            <p className="text-sm text-gray-500 mt-1">Live receiver and single-scan capture</p>
          </div>
          <button className="btn-secondary" onClick={loadDevices} disabled={loading}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh Devices
          </button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-6">
            <div className="card">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold">Receiver</h2>
                <div className={`badge ${listening ? 'badge-info' : 'badge-warning'}`}>
                  {listening ? 'Listening' : 'Idle'}
                </div>
              </div>

              <div className="mt-4 flex items-center gap-2 rounded-xl bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 p-3">
                <div className={`h-3 w-3 rounded-full ${listening ? 'bg-blue-600 animate-pulse' : 'bg-gray-400'}`} />
                <span className="text-sm font-medium text-slate-900">
                  {listening ? 'Status: Listening on port 2115' : 'Status: Not listening'}
                </span>
              </div>

              <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-gray-600">Select Device</label>
                  <select
                    className="input mt-1"
                    value={selectedDevice}
                    onChange={(e) => setSelectedDevice(e.target.value)}
                  >
                    {devices.map((device) => (
                      <option key={device.device_id} value={device.device_id}>
                        {device.name} - {device.ip_address}:{device.port}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-gray-600">Segments per scan</label>
                  <input
                    type="number"
                    className="input mt-1"
                    value={numSegments}
                    onChange={(e) => setNumSegments(e.target.value)}
                    min="1"
                    max="100"
                  />
                </div>
              </div>

              <div className="mt-4 flex gap-2">
                {!listening ? (
                  <button className="btn-primary w-full" onClick={startListening} disabled={loading}>
                    <Play className="h-4 w-4 mr-2" />
                    Start Listening
                  </button>
                ) : (
                  <button className="btn-danger w-full" onClick={stopListening} disabled={loading}>
                    <Square className="h-4 w-4 mr-2" />
                    Stop Listening
                  </button>
                )}
              </div>

              <button
                className="btn-success w-full mt-2"
                onClick={handleReceive}
                disabled={!listening || loading}
              >
                Receive Data
              </button>
            </div>
          </div>

          <div className="space-y-6">
            <div className="card">
              <h2 className="text-lg font-semibold">Last Results</h2>
              {pointCloudData ? (
                <div className="mt-4 grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-xs text-gray-600">Total Points</p>
                    <p className="text-2xl font-bold text-slate-900">{pointCloudData.total_points}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-600">Devices</p>
                    <p className="text-2xl font-bold text-slate-900">{pointCloudData.devices.length}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-600">Segments</p>
                    <p className="text-2xl font-bold text-slate-900">
                      {pointCloudData.segments_per_device}
                    </p>
                  </div>
                </div>
              ) : (
                <p className="mt-4 text-gray-500">No acquisition data yet</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </Layout>
  );
}
