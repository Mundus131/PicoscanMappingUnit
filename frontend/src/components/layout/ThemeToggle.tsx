/**
 * Theme Toggle
 */

'use client';

import React from 'react';
import { Moon, Sun } from 'lucide-react';
import { useTheme } from '@/components/ThemeProvider';

export default function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-800 bg-white/80 dark:bg-gray-900/80 backdrop-blur-lg p-3">
      <div className="text-[11px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">
        Theme
      </div>
      <button
        onClick={toggleTheme}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
        aria-label="Toggle theme"
      >
        <div className="flex items-center gap-2 text-xs font-semibold text-gray-700 dark:text-gray-200">
          {theme === 'dark' ? (
            <>
              <Moon className="w-4 h-4 text-blue-400" />
              Dark mode
            </>
          ) : (
            <>
              <Sun className="w-4 h-4 text-amber-500" />
              Light mode
            </>
          )}
        </div>
        <span className={`w-2.5 h-2.5 rounded-full ${theme === 'dark' ? 'bg-blue-500' : 'bg-amber-500'}`}></span>
      </button>
    </div>
  );
}
