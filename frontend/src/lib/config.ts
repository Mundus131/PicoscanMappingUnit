/**
 * Configuration
 */

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

export const API_ENDPOINTS = {
  // Devices
  devices: {
    list: '/devices',
    get: (id: string) => `/devices/${id}`,
    create: '/devices',
    update: (id: string) => `/devices/${id}`,
    delete: (id: string) => `/devices/${id}`,
    connect: (id: string) => `/devices/${id}/connect`,
    disconnect: (id: string) => `/devices/${id}/disconnect`,
  },
  // Acquisition
  acquisition: {
    startListening: '/acquisition/start-listening',
    stopListening: '/acquisition/stop-listening',
    receiveData: '/acquisition/receive-data',
    status: (id: string) => `/acquisition/receiver-status/${id}`,
    testReceive: (id: string) => `/acquisition/test-receive/${id}`,
    metrics: (id: string) => `/acquisition/receiver-metrics/${id}`,
  },
  // Point Cloud Processing
  pointCloud: {
    merge: '/point-cloud/merge',
    interpolate: '/point-cloud/interpolate-missing-data',
    statistics: '/point-cloud/statistics',
    filter: '/point-cloud/filter',
    measure: (id: string) => `/point-cloud/measure/${id}`,
  },
};

export const UI = {
  REFRESH_INTERVAL: 2000, // ms
  PLOT_CONFIG: {
    responsive: true,
    displayModeBar: true,
    displaylogo: false,
  },
};
