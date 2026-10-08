import { SocietyPlannerView } from "../../components/society/schedule/SocietyPlannerView";

/** Premium-wash visit days for every society of the manager's center. */
export default function ManagerSocietyPlannerPage() {
  return <SocietyPlannerView societyBasePath="/manager/societies" />;
}
