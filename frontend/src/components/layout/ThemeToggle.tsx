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
    <div className="theme-panel">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-100 mb-2">
        Appearance
      </div>
      <button
        onClick={toggleTheme}
        className="theme-btn"
        aria-label="Toggle theme"
      >
        <div className="flex items-center gap-2 text-xs font-semibold">
          {theme === 'dark' ? (
            <>
              <Moon className="w-4 h-4" />
              Dark mode
            </>
          ) : (
            <>
              <Sun className="w-4 h-4" />
              Light mode
            </>
          )}
        </div>
        <span className={`w-2.5 h-2.5 rounded-full ${theme === 'dark' ? 'bg-blue-300' : 'bg-emerald-300'}`}></span>
      </button>
    </div>
  );
}
