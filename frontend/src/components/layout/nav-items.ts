import {
  LayoutDashboard,
  Bot,
  Swords,
  Play,
  Trophy,
  Settings,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

// Agent-centric primary navigation. The old skill-centric items
// (Skills/Toolboxes/Leaderboard) are dropped — the new server has no backend for them.
export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/missions", label: "Missions", icon: Swords },
  { href: "/runs", label: "Runs", icon: Play },
  { href: "/results", label: "Results", icon: Trophy },
];

export const FOOTER_NAV: NavItem[] = [
  { href: "/setup", label: "Setup", icon: Settings },
  { href: "/settings", label: "Settings", icon: SlidersHorizontal },
];
