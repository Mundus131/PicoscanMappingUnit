/**
 * Sidebar Navigation
 */

'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { LayoutDashboard, Radio, BarChart3, Cpu, Sliders, History, X } from 'lucide-react';

const navItems = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard, description: 'Overview & runtime' },
  { href: '/acquisition', label: 'Acquisition', icon: Radio, description: 'Live data capture' },
  { href: '/analytics', label: 'Analytics', icon: BarChart3, description: 'Processing insights' },
  { href: '/history', label: 'History', icon: History, description: 'Archived measurements' },
  { href: '/calibration', label: 'System Config', icon: Sliders, description: 'Frame & device layout' },
];

interface SidebarProps {
  railOpen: boolean;
  onSetRailOpen: (open: boolean) => void;
}

export default function Sidebar({ railOpen, onSetRailOpen }: SidebarProps) {
  const pathname = usePathname();

  return (
    <>
      <div className={`rail-backdrop ${railOpen ? 'show' : ''}`} onClick={() => onSetRailOpen(false)} />
      <aside
        className={`appshell-rail ${railOpen ? 'rail-open' : ''}`}
        onMouseEnter={() => onSetRailOpen(true)}
        onMouseLeave={() => onSetRailOpen(false)}
      >
        <div className="sidebar-brand">
          <div className="h-10 w-10 rounded-md bg-white/15 flex items-center justify-center">
            <Cpu className="h-5 w-5" />
          </div>
          <div className="sidebar-brand-text">
            <div className="text-base font-semibold leading-tight">PicoScan</div>
            <div className="text-xs text-slate-200">Mapping Unit</div>
          </div>
          <button className="btn-secondary btn-sm btn-icon ml-auto md:hidden" onClick={() => onSetRailOpen(false)}>
            <X className="h-4 w-4" />
          </button>
        </div>

        <nav className="sidebar-nav p-3 flex flex-col gap-2 flex-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = pathname === item.href;

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`sidebar-nav-link ${isActive ? 'active' : ''}`}
              >
                <div className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-black/15">
                  <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0 rail-labels">
                  <span className="sidebar-label block text-sm">{item.label}</span>
                  <span className="sidebar-nav-meta block truncate">{item.description}</span>
                </div>
              </Link>
            );
          })}
        </nav>
      </aside>
    </>
  );
}
