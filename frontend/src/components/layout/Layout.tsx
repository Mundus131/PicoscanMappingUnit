/**
 * Layout - Main Shell
 */

'use client';

import React from 'react';
import Header from './Header';
import Sidebar from './Sidebar';
import { usePathname } from 'next/navigation';

interface LayoutProps {
  children: React.ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  const [railOpen, setRailOpen] = React.useState(false);
  const pathname = usePathname();

  React.useEffect(() => {
    setRailOpen(false);
  }, [pathname]);

  return (
    <div className="shell appshell">
      <Header railOpen={railOpen} onToggleRail={() => setRailOpen((v) => !v)} />
      <div className="appshell-main">
        <Sidebar railOpen={railOpen} onSetRailOpen={setRailOpen} />
        <main className="appshell-content">
          <div className="content-wrap">{children}</div>
        </main>
      </div>
    </div>
  );
}
