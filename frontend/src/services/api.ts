/**
 * API Service - komunikacja z backendem
 */

import axios from 'axios';
import { API_BASE_URL, API_ENDPOINTS } from '@/lib/config';
import type {
  Device,
  AcquisitionResponse,
  ReceiverStatus,
  ListeningResponse,
} from '@/types';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Devices
export const deviceService = {
  getAll: () => api.get<Device[]>(API_ENDPOINTS.devices.list),
  get: (id: string) => api.get<Device>(API_ENDPOINTS.devices.get(id)),
  create: (device: Partial<Device>) => api.post<Device>(API_ENDPOINTS.devices.create, device),
  update: (id: string, device: Partial<Device>) => 
    api.put<Device>(API_ENDPOINTS.devices.update(id), device),
  delete: (id: string) => api.delete(API_ENDPOINTS.devices.delete(id)),
  connect: (id: string) => api.post(`${API_ENDPOINTS.devices.connect(id)}`),
  disconnect: (id: string) => api.post(`${API_ENDPOINTS.devices.disconnect(id)}`),
};

// Acquisition (receiver mode)
export const acquisitionService = {
  startListening: () => 
    api.post<ListeningResponse>(API_ENDPOINTS.acquisition.startListening),
  
  stopListening: () => 
    api.post<ListeningResponse>(API_ENDPOINTS.acquisition.stopListening),
  
  receiveData: (deviceIds: string[], numSegments: number = 1) =>
    api.post<AcquisitionResponse>(API_ENDPOINTS.acquisition.receiveData, {
      device_ids: deviceIds,
      num_segments: numSegments,
    }),
  
  getStatus: (deviceId: string) =>
    api.get<ReceiverStatus>(API_ENDPOINTS.acquisition.status(deviceId)),
  
  testReceive: (deviceId: string, numSegments: number = 1) =>
    api.post(API_ENDPOINTS.acquisition.testReceive(deviceId), {
      num_segments: numSegments,
    }),

  getMetrics: (deviceId: string) =>
    api.get(API_ENDPOINTS.acquisition.metrics(deviceId)),
};

// Point Cloud Processing
export const pointCloudService = {
  merge: (deviceIds: string[]) =>
    api.post('/point-cloud/merge', { device_ids: deviceIds }),
  
  interpolate: (deviceIds: string[], method: string = 'nearest', gridSpacing: number = 5.0) =>
    api.post('/point-cloud/interpolate-missing-data', {
      device_ids: deviceIds,
      method,
      grid_spacing: gridSpacing,
    }),
  
  getStatistics: (deviceIds: string[]) =>
    api.post('/point-cloud/statistics', { device_ids: deviceIds }),
  
  filter: (deviceIds: string[], center: [number, number, number], maxDistance: number) =>
    api.post('/point-cloud/filter', {
      device_ids: deviceIds,
      center,
      max_distance: maxDistance,
    }),
};

export default api;
