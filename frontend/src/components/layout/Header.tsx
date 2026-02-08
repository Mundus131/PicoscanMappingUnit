/**
 * Header Component
 */

'use client';

import React from 'react';
import Link from 'next/link';
import { Activity, Radio, Settings } from 'lucide-react';

export default function Header() {
  return (
    <header className="sticky top-0 z-40 animate-slide-in-down">
      <div className="bg-white/80 dark:bg-gray-900/80 backdrop-blur-xl border-b border-gray-200/60 dark:border-gray-800/60 shadow-lg">
        <div className="flex items-center justify-between px-6 py-3">
          <div className="flex items-center gap-3">
            <div className="relative">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-blue-600 to-purple-600 flex items-center justify-center shadow-lg">
                <Activity className="w-6 h-6 text-white" strokeWidth={2.5} />
              </div>
              <div className="absolute -bottom-1 -right-1 w-3 h-3 rounded-full border-2 border-white dark:border-gray-900 bg-green-500 animate-pulse"></div>
            </div>
            <div>
              <h1 className="text-lg font-bold text-gray-900 dark:text-white">
                PicoScan Mapping Unit
              </h1>
              <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                <Radio className="w-3 h-3 text-blue-500" />
                Real-time LiDAR acquisition
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="hidden md:flex items-center gap-2 rounded-xl bg-gradient-to-r from-green-50 to-emerald-50 dark:from-green-900/20 dark:to-emerald-900/20 border border-green-200 dark:border-green-800 px-3 py-1.5">
              <div className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
              </div>
              <span className="text-xs font-medium text-green-700 dark:text-green-300">Connected</span>
            </div>
            <Link
              href="/calibration"
              className="p-2 rounded-xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 transition-all duration-200 shadow-sm hover:shadow group"
              aria-label="System Config"
            >
              <Settings className="w-5 h-5 text-gray-600 dark:text-gray-300 group-hover:text-blue-600 transition-colors" />
            </Link>
          </div>
        </div>
        <div className="h-1 bg-gradient-to-r from-blue-600 via-purple-600 to-pink-600 opacity-70"></div>
      </div>
    </header>
  );
}
