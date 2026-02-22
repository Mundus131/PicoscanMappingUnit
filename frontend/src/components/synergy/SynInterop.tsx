'use client';

import React, { useEffect, useMemo, useRef } from 'react';

type ButtonVariant = 'filled' | 'danger' | 'success' | 'default';
type SynToggleElement = HTMLElement & { checked?: boolean; disabled?: boolean };

export function SynInteropButton({
  children,
  onPress,
  disabled,
  variant,
  size,
  className,
}: {
  children: React.ReactNode;
  onPress: () => void;
  disabled?: boolean;
  variant?: ButtonVariant;
  size?: 'small' | 'medium' | 'large';
  className?: string;
}) {
  const ref = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const handler = (ev: Event) => {
      ev.preventDefault();
      if (!disabled) onPress();
    };
    el.addEventListener('click', handler);
    return () => el.removeEventListener('click', handler);
  }, [onPress, disabled]);

  return (
    <syn-button
      ref={ref as React.RefObject<HTMLElement>}
      className={className}
      variant={variant === 'default' ? undefined : variant}
      size={size}
      disabled={disabled}
    >
      {children}
    </syn-button>
  );
}

export function SynInteropSwitch({
  checked,
  onToggle,
  disabled,
  children,
  className,
}: {
  checked: boolean;
  onToggle: (next: boolean) => void;
  disabled?: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const el = ref.current as SynToggleElement | null;
    if (!el) return;
    el.checked = checked;
    el.disabled = !!disabled;
  }, [checked, disabled]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const handler = (ev: Event) => {
      ev.preventDefault();
      if (!disabled) onToggle(!checked);
    };
    el.addEventListener('click', handler);
    return () => el.removeEventListener('click', handler);
  }, [checked, disabled, onToggle]);

  return (
    <syn-switch ref={ref as React.RefObject<HTMLElement>} className={className} checked={checked} disabled={disabled}>
      {children}
    </syn-switch>
  );
}

export function SynInteropCheckbox({
  checked,
  onToggle,
  disabled,
  children,
  className,
}: {
  checked: boolean;
  onToggle: (next: boolean) => void;
  disabled?: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const el = ref.current as SynToggleElement | null;
    if (!el) return;
    el.checked = checked;
    el.disabled = !!disabled;
  }, [checked, disabled]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const handler = (ev: Event) => {
      ev.preventDefault();
      if (!disabled) onToggle(!checked);
    };
    el.addEventListener('click', handler);
    return () => el.removeEventListener('click', handler);
  }, [checked, disabled, onToggle]);

  return (
    <syn-checkbox ref={ref as React.RefObject<HTMLElement>} className={className} checked={checked} disabled={disabled}>
      {children}
    </syn-checkbox>
  );
}

export function SynInteropDropdown({
  value,
  options,
  onChange,
  placeholder = 'Select',
  className,
}: {
  value: string;
  options: Array<{ value: string; label: string; disabled?: boolean }>;
  onChange: (next: string) => void;
  placeholder?: string;
  className?: string;
}) {
  const menuRef = useRef<HTMLElement | null>(null);

  const activeLabel = useMemo(
    () => options.find((o) => o.value === value)?.label || placeholder,
    [options, value, placeholder]
  );

  useEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    const handler = (ev: Event) => {
      const target = ev.target as HTMLElement | null;
      const item = target?.closest?.('syn-menu-item') as HTMLElement | null;
      if (!item) return;
      const next = item.getAttribute('data-value');
      const itemDisabled = item.hasAttribute('disabled');
      if (!next || itemDisabled) return;
      onChange(next);
    };
    menu.addEventListener('click', handler);
    return () => menu.removeEventListener('click', handler);
  }, [onChange]);

  return (
    <div className={className} style={{ position: 'relative' }}>
      <syn-dropdown>
        <syn-button slot="trigger" caret="">
          {activeLabel}
        </syn-button>
        <syn-menu ref={menuRef as React.RefObject<HTMLElement>} style={{ minWidth: 240 }}>
          {options.map((opt) => (
            <syn-menu-item
              key={opt.value}
              data-value={opt.value}
              type="checkbox"
              checked={opt.value === value}
              disabled={opt.disabled}
            >
              {opt.label}
            </syn-menu-item>
          ))}
        </syn-menu>
      </syn-dropdown>
    </div>
  );
}
