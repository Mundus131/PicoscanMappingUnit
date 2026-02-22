'use client';

import { useEffect } from 'react';

export default function SynergyProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    const ensureDropdownCarets = () => {
      const triggers = document.querySelectorAll('syn-dropdown > syn-button[slot="trigger"]');
      triggers.forEach((el) => {
        if (!el.hasAttribute('caret')) {
          el.setAttribute('caret', '');
        }
      });
    };

    // Register all Synergy components explicitly.
    // Autoloader can fail in bundled environments due dynamic base path resolution.
    import('@synergy-design-system/components/synergy.js').catch(() => {
      // Fallback to autoloader path when full bundle import is unavailable.
      import('@synergy-design-system/components/synergy-autoloader.js').catch(() => {
        // Keep UI rendering via fallback CSS if registration fails.
      });
    });

    ensureDropdownCarets();
    const observer = new MutationObserver(() => {
      ensureDropdownCarets();
    });
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  return <>{children}</>;
}
