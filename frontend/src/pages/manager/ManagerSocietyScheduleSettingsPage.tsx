import { ScheduleSettingsView } from "../../components/society/schedule/ScheduleSettingsView";

/** Manager: the global society-schedule settings, read-only (an admin changes them). */
export default function ManagerSocietyScheduleSettingsPage() {
  return <ScheduleSettingsView canEdit={false} plannerPath="/manager/society-planner" />;
}
