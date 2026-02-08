/**
 * Acquisition Control Panel
 */

'use client';

import React, { useState } from 'react';
import { Play, Square, Download } from 'lucide-react';
import toast from 'react-hot-toast';
import type { Device } from '@/types';

interface AcquisitionControlProps {
  devices: Device[];
  listening: boolean;
  loading: boolean;
  onStartListening: () => Promise<void>;
  onStopListening: () => Promise<void>;
  onReceiveData: (deviceIds: string[]) => Promise<void>;
}

export default function AcquisitionControl({
  devices,
  listening,
  loading,
  onStartListening,
  onStopListening,
  onReceiveData,
}: AcquisitionControlProps) {
  const [selectedDevices, setSelectedDevices] = useState<Set<string>>(
    new Set(devices.map((d) => d.device_id).slice(0, 1))
  );

  const handleStartListening = async () => {
    try {
      await onStartListening();
      toast.success('Started listening for data');
    } catch (error) {
      toast.error('Failed to start listening');
    }
  };

  const handleStopListening = async () => {
    try {
      await onStopListening();
      toast.success('Stopped listening');
    } catch (error) {
      toast.error('Failed to stop listening');
    }
  };

  const handleReceiveData = async () => {
    try {
      const deviceIds = Array.from(selectedDevices);
      if (deviceIds.length === 0) {
        toast.error('Select at least one device');
        return;
      }
      await onReceiveData(deviceIds);
      toast.success('Data received successfully');
    } catch (error) {
      toast.error('Failed to receive data');
    }
  };

  return (
    <div className="card">
      <h3 className="text-lg font-semibold text-slate-900">Acquisition</h3>

      <div className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 p-3">
        <div
          className={`h-3 w-3 rounded-full ${
            listening ? 'bg-blue-600 animate-pulse' : 'bg-gray-400'
          }`}
        />
        <span className="text-sm font-medium text-slate-900">
          {listening ? 'Listening...' : 'Not listening'}
        </span>
      </div>

      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium text-slate-900">Select Devices</label>
        <select
          className="input"
          multiple
          value={Array.from(selectedDevices)}
          onChange={(e) => {
            const options = Array.from(e.target.selectedOptions).map((o) => o.value);
            setSelectedDevices(new Set(options));
          }}
        >
          {devices.map((device) => (
            <option key={device.device_id} value={device.device_id}>
              {device.name} ({device.ip_address}:{device.port})
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-2">
        {!listening ? (
          <button
            className="btn-primary w-full"
            onClick={handleStartListening}
            disabled={loading}
          >
            <Play className="h-4 w-4 mr-2" />
            Start Listening
          </button>
        ) : (
          <button
            className="btn-danger w-full"
            onClick={handleStopListening}
            disabled={loading}
          >
            <Square className="h-4 w-4 mr-2" />
            Stop Listening
          </button>
        )}

        <button
          className="btn-success w-full"
          onClick={handleReceiveData}
          disabled={loading || !listening || selectedDevices.size === 0}
        >
          <Download className="h-4 w-4 mr-2" />
          Receive Data
        </button>
      </div>
    </div>
  );
}
