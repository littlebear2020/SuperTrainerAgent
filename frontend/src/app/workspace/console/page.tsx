import { ConsoleRolePage } from "@/components/workspace/console/console-role-page";

/**
 * Role-specific usage page at `/workspace/console`, nested under the workspace
 * layout where auth + AuthProvider are already in place.
 */
export default function WorkspaceConsolePage() {
  return <ConsoleRolePage />;
}
