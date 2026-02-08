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
    <aside className="relative w-80 border-r border-gray-200/50 dark:border-gray-800/60 bg-gradient-to-b from-white via-gray-50 to-white dark:from-gray-950 dark:via-gray-900 dark:to-gray-950 shadow-2xl flex flex-col">
      <div className="p-6 border-b border-gray-200/50 dark:border-gray-800/60">
        <div className="flex items-center gap-3">
          <div className="relative">
            <div className="absolute inset-0 bg-gradient-to-br from-blue-600 to-purple-600 rounded-2xl blur-lg opacity-50"></div>
            <div className="relative w-12 h-12 bg-gradient-to-br from-blue-600 via-indigo-600 to-purple-600 rounded-2xl flex items-center justify-center shadow-2xl">
              <Cpu className="w-6 h-6 text-white" />
            </div>
          </div>
          <div>
            <div className="text-xl font-black text-gradient-primary">PicoScan</div>
            <div className="text-xs text-gray-500 dark:text-gray-400">Mapping Unit</div>
          </div>
        </div>
      </div>

      <nav className="p-4 space-y-2 flex-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = pathname === item.href;

          return (
            <Link
              key={item.href}
              href={item.href}
              className={`group relative flex items-center px-4 py-3.5 text-sm font-semibold rounded-xl transition-all duration-300 overflow-hidden ${
                isActive
                  ? 'bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-lg shadow-blue-500/30'
                  : 'text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800/70'
              }`}
            >
              {!isActive && (
                <div className="absolute inset-0 bg-gradient-to-r from-blue-500/10 to-indigo-500/10 opacity-0 group-hover:opacity-100 transition-opacity duration-300"></div>
              )}
              <div
                className={`relative flex items-center justify-center w-9 h-9 rounded-lg mr-3 transition-all duration-300 ${
                  isActive ? 'bg-white/20' : 'bg-gray-100 dark:bg-gray-800 group-hover:bg-gray-200 dark:group-hover:bg-gray-700'
                }`}
              >
                <Icon className={`h-5 w-5 ${isActive ? 'text-white' : 'text-gray-500 dark:text-gray-300 group-hover:text-blue-600'}`} />
              </div>
              <div className="relative flex-1 min-w-0">
                <span className="block">{item.label}</span>
                <span className={`block text-xs font-normal ${isActive ? 'text-white/80' : 'text-gray-500 dark:text-gray-400'} truncate`}>
                  {item.description}
                </span>
              </div>
              {isActive && <div className="absolute right-2 w-1.5 h-8 bg-white rounded-full"></div>}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto p-4">
        <ThemeToggle />
      </div>
    </aside>
  );
}
