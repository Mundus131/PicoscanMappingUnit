/**
 * API Types - synchronizowane z backendem
 */

export interface Calibration {
  translation: [number, number, number];
  rotation_deg: [number, number, number];
  scale: number;
}

export interface Device {
  device_id: string;
  name: string;
  ip_address: string;
  port: number;
  enabled: boolean;
  connection_status: string;
  calibration: Calibration;
  acquisition_mode: string;
  encoder_enabled: boolean;
  speed_profile: string;
}

export interface Point3D {
  x: number;
  y: number;
  z: number;
}

export interface Bounds {
  min: Point3D;
  max: Point3D;
  size: Point3D;
}

export interface PointCloudStatistics {
  num_points: number;
  centroid: Point3D;
  bounds: Bounds;
  density: number | null;
}

export interface AcquisitionResponse {
  message: string;
  total_points: number;
  devices: string[];
  segments_per_device: number;
  statistics: PointCloudStatistics;
}

export interface ReceiverStatus {
  device_id: string;
  listening: boolean;
  info: {
    listen_ip: string;
    listen_port: number;
    listening: boolean;
    format: string;
  };
}

export interface ListeningResponse {
  message: string;
  results: Record<string, boolean>;
  info?: string;
}

export interface PointCloudData {
  x: number[];
  y: number[];
  z: number[];
  mode: string;
  type: string;
  name: string;
}
