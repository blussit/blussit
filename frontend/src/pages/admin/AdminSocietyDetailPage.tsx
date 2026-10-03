import { useParams } from "react-router-dom";
import { SocietyDetailView } from "../../components/society/SocietyDetailView";

export default function AdminSocietyDetailPage() {
  const { id = "" } = useParams();
  return <SocietyDetailView societyId={id} backTo="/admin/societies" isAdmin />;
}
