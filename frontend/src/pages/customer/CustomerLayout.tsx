import { Outlet } from "react-router-dom";
import { CustomerShell } from "../../components/customer/CustomerShell";

/**
 * The customer app: Home, Bookings, Plans and Profile as bottom tabs on a
 * phone/tablet, a sidebar from 1024px (see CustomerShell).
 */
export default function CustomerLayout() {
  return (
    <CustomerShell>
      <Outlet />
    </CustomerShell>
  );
}
