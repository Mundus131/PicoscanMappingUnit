/**
 * Layout - Main Shell
 */

'use client';

import React from 'react';
import Header from './Header';
import Sidebar from './Sidebar';

interface LayoutProps {
  children: React.ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  return (
    <div className="flex h-screen app-shell text-slate-900 dark:text-gray-100">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-auto">
          <div className="mx-auto max-w-7xl px-6 py-8">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
