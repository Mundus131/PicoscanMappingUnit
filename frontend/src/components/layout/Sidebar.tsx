/**
 * Sidebar Navigation
 */

'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Box, Radio, BarChart3, Cpu, Sliders, History } from 'lucide-react';
import ThemeToggle from './ThemeToggle';

const navItems = [
  { href: '/', label: 'Dashboard', icon: Box, description: 'Overview & stats' },
  { href: '/acquisition', label: 'Acquisition', icon: Radio, description: 'Live data capture' },
  { href: '/analytics', label: 'Analytics', icon: BarChart3, description: 'Processing insights' },
  { href: '/history', label: 'History', icon: History, description: 'Archived measurements' },
  { href: '/calibration', label: 'System Config', icon: Sliders, description: 'Frame & device layout' },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="shell-sidebar flex flex-col">
      <div className="sidebar-brand">
        <div className="h-10 w-10 rounded-md bg-white/15 flex items-center justify-center">
          <Cpu className="h-5 w-5" />
        </div>
        <div className="sidebar-brand-text">
          <div className="text-base font-semibold leading-tight">PicoScan</div>
          <div className="text-xs text-slate-200">Mapping Unit</div>
        </div>
      </div>

      <nav className="p-3 space-y-2 flex-1">
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
              <div className="min-w-0">
                <span className="sidebar-label block text-sm">{item.label}</span>
                <span className="sidebar-nav-meta block truncate">{item.description}</span>
              </div>
            </Link>
          );
        })}
      </nav>

      <div className="p-3 mt-auto">
        <ThemeToggle />
      </div>
    </aside>
  );
}
