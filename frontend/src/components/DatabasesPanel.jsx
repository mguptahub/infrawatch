import { useState } from "react";
import RDSPanel from "./RDSPanel";
import DocumentDBPanel from "./DocumentDBPanel";
import AlertBanner from "./AlertBanner";

/**
 * Databases tab content: RDS (Aurora, standalone instances) and DocumentDB (clusters, instances).
 */
export default function DatabasesPanel() {
  const [resourceAlarms, setResourceAlarms] = useState([]);
  return (
    <>
      <AlertBanner serviceType="databases" onAlarmsLoaded={setResourceAlarms} />
      <RDSPanel title="RDS" resourceAlarms={resourceAlarms} />
      <DocumentDBPanel resourceAlarms={resourceAlarms} />
    </>
  );
}
