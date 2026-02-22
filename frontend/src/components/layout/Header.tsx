/**
 * Header Component
 */

'use client';

import React from 'react';
import Link from 'next/link';
import { Menu, SlidersHorizontal, Radio, Activity } from 'lucide-react';
import { usePathname } from 'next/navigation';

interface HeaderProps {
  railOpen: boolean;
  onToggleRail: () => void;
}

const TITLES: Array<{ match: RegExp; title: string; subtitle: string }> = [
  { match: /^\/acquisition/, title: 'Acquisition', subtitle: 'Live capture and stream health' },
  { match: /^\/analytics/, title: 'Analytics', subtitle: 'Post-processing and result export' },
  { match: /^\/history/, title: 'History', subtitle: 'Archived captures and traceability' },
  { match: /^\/calibration/, title: 'System Config', subtitle: 'Device layout and runtime settings' },
  { match: /^\//, title: 'Dashboard', subtitle: 'System overview' },
];

export default function Header({ railOpen, onToggleRail }: HeaderProps) {
  const pathname = usePathname();
  const titleCfg = TITLES.find((t) => t.match.test(pathname || '/')) || TITLES[TITLES.length - 1];

  return (
    <header className="header appshell-header">
      <div className="flex min-w-0 items-center gap-3 sm:gap-4">
        <button
          className="btn-secondary btn-icon md:hidden"
          aria-label={railOpen ? 'Close navigation' : 'Open navigation'}
          onClick={onToggleRail}
        >
          <Menu className="h-4 w-4" />
        </button>
        <div className="inline-flex h-10 items-center border-r border-slate-300 pr-3">
          <svg viewBox="0 0 170 50" role="img" aria-label="SICK" style={{ width: 94, height: 30 }}>
            <text x="0" y="37" fill="#0082ca" fontSize="42" fontWeight="900" fontFamily="Arial Black, Segoe UI, sans-serif">SICK</text>
          </svg>
        </div>
        <div className="min-w-0">
          <div className="shell-title">{titleCfg.title}</div>
          <div className="shell-subtitle truncate">{titleCfg.subtitle}</div>
        </div>
      </div>

      <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:gap-3">
        <div className="status-pill">
          <span className="status-dot" />
          System online
        </div>
        <Link
          href="/acquisition"
          className="btn-secondary btn-icon"
          aria-label="Acquisition"
          title="Acquisition"
        >
          <Radio className="h-4 w-4" />
        </Link>
        <Link
          href="/calibration"
          className="btn-secondary btn-icon"
          aria-label="System Config"
          title="System Config"
        >
          <SlidersHorizontal className="h-4 w-4" />
        </Link>
        <div className="hidden sm:flex items-center gap-2 text-xs text-slate-500">
          <Activity className="h-3.5 w-3.5" />
          live
        </div>
      </div>
    </header>
  );
}
