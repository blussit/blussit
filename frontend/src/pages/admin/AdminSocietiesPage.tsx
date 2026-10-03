import { SocietyListView } from "../../components/society/SocietyListView";

export default function AdminSocietiesPage() {
  return <SocietyListView basePath="/admin/societies" isAdmin />;
}
