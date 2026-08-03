"use client";

import {
  createContext,
  useContext,
  useState,
  type HTMLAttributes,
  type ReactNode,
} from "react";
import { cn } from "./cn";

interface TabsContextValue {
  value: string;
  setValue: (value: string) => void;
}

const TabsContext = createContext<TabsContextValue | null>(null);

function useTabs() {
  const ctx = useContext(TabsContext);
  if (!ctx) throw new Error("Tabs components must be used within <Tabs>");
  return ctx;
}

export interface TabsProps extends Omit<HTMLAttributes<HTMLDivElement>, "onChange"> {
  /** Controlled active tab value. */
  value?: string;
  /** Initial active tab value when uncontrolled. */
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  children?: ReactNode;
}

export function Tabs({
  className,
  value,
  defaultValue,
  onValueChange,
  children,
  ...props
}: TabsProps) {
  const [internal, setInternal] = useState(defaultValue ?? "");
  const active = value ?? internal;
  const setValue = (next: string) => {
    if (value === undefined) setInternal(next);
    onValueChange?.(next);
  };
  return (
    <TabsContext.Provider value={{ value: active, setValue }}>
      <div className={cn("flex flex-col gap-3", className)} {...props}>
        {children}
      </div>
    </TabsContext.Provider>
  );
}

export function TabsList({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex items-center self-start overflow-hidden rounded-md border border-border font-mono text-caption",
        className,
      )}
      {...props}
    />
  );
}

export interface TabsTriggerProps
  extends HTMLAttributes<HTMLButtonElement> {
  value: string;
}

export function TabsTrigger({
  className,
  value,
  ...props
}: TabsTriggerProps) {
  const ctx = useTabs();
  const active = ctx.value === value;
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={() => ctx.setValue(value)}
      className={cn(
        "border-r border-border/60 px-3.5 py-1.5 transition-colors last:border-r-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        active
          ? "bg-raised text-primary"
          : "text-text-secondary hover:text-foreground",
        className,
      )}
      {...props}
    />
  );
}

export interface TabsContentProps extends HTMLAttributes<HTMLDivElement> {
  value: string;
}

export function TabsContent({
  className,
  value,
  ...props
}: TabsContentProps) {
  const ctx = useTabs();
  if (ctx.value !== value) return null;
  return (
    <div
      role="tabpanel"
      className={cn("animate-fade-up text-body", className)}
      {...props}
    />
  );
}
