import { useParams } from "react-router-dom";
import { SocietyDetailView } from "../../components/society/SocietyDetailView";

export default function ManagerSocietyDetailPage() {
  const { id = "" } = useParams();
  return <SocietyDetailView societyId={id} backTo="/manager/societies" />;
}
