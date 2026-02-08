/**
 * Custom hook do komunikacji z Picoscanem
 */

import { useState, useCallback } from 'react';
import { acquisitionService, deviceService } from '@/services/api';
import type { Device, AcquisitionResponse, ReceiverStatus } from '@/types';

export const usePicoscan = () => {
  const [devices, setDevices] = useState<Device[]>([]);
  const [listening, setListening] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pointCloudData, setPointCloudData] = useState<AcquisitionResponse | null>(null);

  // Pobierz listę urządzeń
  const loadDevices = useCallback(async () => {
    try {
      setLoading(true);
      const response = await deviceService.getAll();
      setDevices(response.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load devices');
    } finally {
      setLoading(false);
    }
  }, []);

  // Uruchom nasłuchiwanie
  const startListening = useCallback(async () => {
    try {
      setLoading(true);
      await acquisitionService.startListening();
      setListening(true);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start listening');
      setListening(false);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  // Zatrzymaj nasłuchiwanie
  const stopListening = useCallback(async () => {
    try {
      setLoading(true);
      await acquisitionService.stopListening();
      setListening(false);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to stop listening');
    } finally {
      setLoading(false);
    }
  }, []);

  // Odbierz dane
  const receiveData = useCallback(async (deviceIds: string[]) => {
    try {
      setLoading(true);
      const response = await acquisitionService.receiveData(deviceIds);
      setPointCloudData(response.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to receive data');
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  // Pobierz status
  const getStatus = useCallback(async (deviceId: string) => {
    try {
      const response = await acquisitionService.getStatus(deviceId);
      setError(null);
      return response.data;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to get status');
      throw err;
    }
  }, []);

  return {
    devices,
    listening,
    loading,
    error,
    pointCloudData,
    loadDevices,
    startListening,
    stopListening,
    receiveData,
    getStatus,
  };
};
