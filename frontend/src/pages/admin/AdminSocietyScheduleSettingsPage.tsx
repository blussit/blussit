import { ScheduleSettingsView } from "../../components/society/schedule/ScheduleSettingsView";

/** Admin: the global society-schedule settings (editable). */
export default function AdminSocietyScheduleSettingsPage() {
  return <ScheduleSettingsView canEdit plannerPath="/admin/society-planner" />;
}
