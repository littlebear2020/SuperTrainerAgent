import { ConsolePage } from "@/components/workspace/console/console-page";

/**
 * Operations console at `/workspace/console`.
 *
 * Nests under the workspace layout, so auth + AuthProvider are already in
 * place — the page component reads admin status from that context.
 */
export default function WorkspaceConsolePage() {
  return <ConsolePage />;
}
