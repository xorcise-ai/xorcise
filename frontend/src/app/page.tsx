import type { Metadata } from "next";
import { HomeRouter } from "./home-router";

// `/` resolves to the Dashboard for a set-up operator and to Welcome on a fresh
// install; the Dashboard is the route's identity, so that is the title.
//
// Spelled out in full, unlike every other route: a layout's `title.template`
// applies to child segments only, and this page shares the root layout's
// segment — so "Dashboard" alone would render as "Dashboard", losing the brand.
export const metadata: Metadata = { title: "Dashboard · XORCISE" };

export default function Home() {
  return <HomeRouter />;
}
