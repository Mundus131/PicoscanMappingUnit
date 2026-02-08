/**
 * Utility functions
 */

export const formatNumber = (value: number, decimals: number = 2): string => {
  return Number(value).toFixed(decimals);
};

export const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
};

export const getColorByValue = (value: number, max: number): string => {
  const ratio = Math.max(0, Math.min(1, value / max));
  
  if (ratio < 0.33) return '#3b82f6'; // blue
  if (ratio < 0.66) return '#8b5cf6'; // purple
  return '#ef4444'; // red
};

export const sleep = (ms: number): Promise<void> => 
  new Promise(resolve => setTimeout(resolve, ms));
