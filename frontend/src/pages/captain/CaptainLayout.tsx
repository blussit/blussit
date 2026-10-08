import { Outlet } from "react-router-dom";
import { CaptainShell } from "../../components/captain/CaptainShell";
import { CaptainI18nProvider } from "../../context/i18n/CaptainI18nContext";

/** Captain app: Today · Jobs · Earnings · Profile (see CaptainShell). */
export default function CaptainLayout() {
  return (
    <CaptainI18nProvider>
      <CaptainShell>
        <Outlet />
      </CaptainShell>
    </CaptainI18nProvider>
  );
}
