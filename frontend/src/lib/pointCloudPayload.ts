'use client';

type PointLike = number[];

interface PointCloudPayloadLike {
  points?: PointLike[];
  points_blob_b64?: string;
  points_encoding?: string;
  point_stride?: number;
}

function decodeBase64ToFloat32(base64: string): Float32Array {
  const binary = window.atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Float32Array(bytes.buffer);
}

export function decodePointCloudPayload(payload: PointCloudPayloadLike | null | undefined): PointLike[] {
  if (!payload) return [];
  if (Array.isArray(payload.points) && payload.points.length > 0) {
    return payload.points;
  }
  if (payload.points_encoding !== 'float32_base64' || typeof payload.points_blob_b64 !== 'string' || !payload.points_blob_b64) {
    return [];
  }
  const stride = Math.max(3, Number(payload.point_stride || 4));
  const flat = decodeBase64ToFloat32(payload.points_blob_b64);
  if (!Number.isFinite(stride) || stride <= 0) return [];
  const count = Math.floor(flat.length / stride);
  const points: PointLike[] = new Array(count);
  for (let i = 0; i < count; i += 1) {
    const offset = i * stride;
    const row = new Array(stride);
    for (let j = 0; j < stride; j += 1) {
      row[j] = flat[offset + j];
    }
    points[i] = row;
  }
  return points;
}
