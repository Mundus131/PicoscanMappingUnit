'use client';

import { ThemeProvider } from '@/components/ThemeProvider';
import SynergyProvider from '@/components/SynergyProvider';

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SynergyProvider>
      <ThemeProvider>{children}</ThemeProvider>
    </SynergyProvider>
  );
}
