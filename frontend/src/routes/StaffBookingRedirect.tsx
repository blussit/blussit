import { Navigate, useParams } from "react-router-dom";

/** /manager/bookings/:id and /admin/bookings/:id used to render the
 * CUSTOMER detail page — read-only for staff, with none of the
 * assign/reschedule/cancel actions. The queue's ?highlight= param is the
 * canonical staff deep-link, so route the id there. */
export function StaffBookingRedirect({ base }: { base: string }) {
  const { id } = useParams();
  return <Navigate to={id ? `${base}?highlight=${id}` : base} replace />;
}
