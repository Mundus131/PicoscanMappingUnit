/**
 * Root Layout
 */

import type { Metadata } from 'next';
import { Providers } from './providers';
import './globals.css';

export const metadata: Metadata = {
  title: 'PicoScan Mapping Unit',
  description: 'SICK Picoscan LIDAR 3D Point Cloud Acquisition & Processing',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="antialiased bg-gray-50 dark:bg-gray-950 text-slate-900 dark:text-gray-100 transition-colors duration-300">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
