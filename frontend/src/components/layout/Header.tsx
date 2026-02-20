/**
 * Header Component
 */

'use client';

import React from 'react';
import Link from 'next/link';
import { Radio, Settings } from 'lucide-react';

export default function Header() {
  return (
    <header className="shell-header">
      <div className="flex items-center gap-4">
        <div className="inline-flex h-10 items-center border-r border-slate-300 pr-3">
          <svg viewBox="0 0 170 50" role="img" aria-label="SICK" style={{ width: 94, height: 30 }}>
            <text x="0" y="37" fill="#0082ca" fontSize="42" fontWeight="900" fontFamily="Arial Black, Segoe UI, sans-serif">SICK</text>
          </svg>
        </div>
        <div>
          <div className="shell-title">PicoScan Mapping Unit</div>
          <div className="shell-subtitle">Real-time LiDAR acquisition and analysis</div>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <div className="status-pill">
          <span className="status-dot" />
          Connected
        </div>
        <Link
          href="/calibration"
          className="btn-secondary p-2"
          aria-label="System Config"
          title="System Config"
        >
          <Settings className="h-4 w-4" />
        </Link>
        <div className="hidden sm:flex items-center gap-2 text-xs text-slate-500">
          <Radio className="h-3.5 w-3.5" />
          live
        </div>
      </div>
    </header>
  );
}
