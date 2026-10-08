import { SocietyPlannerView } from "../../components/society/schedule/SocietyPlannerView";

/** Premium-wash visit days per center (admin picks the center). */
export default function AdminSocietyPlannerPage() {
  return <SocietyPlannerView societyBasePath="/admin/societies" isAdmin />;
}
