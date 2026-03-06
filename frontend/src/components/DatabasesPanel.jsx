import RDSPanel from "./RDSPanel";
import DocumentDBPanel from "./DocumentDBPanel";

/**
 * Databases tab content: RDS (Aurora, standalone instances) and DocumentDB (clusters, instances).
 */
export default function DatabasesPanel() {
  return (
    <>
      <RDSPanel title="RDS" />
      <DocumentDBPanel />
    </>
  );
}
