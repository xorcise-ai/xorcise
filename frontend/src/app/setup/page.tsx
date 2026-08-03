import type { Metadata } from "next";
import { Welcome } from "@/features/setup/welcome";

export const metadata: Metadata = { title: "Setup" };

/** /setup is the "Get started" landing — reachable from the nav and shown at /
 *  for a fresh operator. See the Welcome component + the Home router. */
export default function SetupPage() {
  return <Welcome />;
}
