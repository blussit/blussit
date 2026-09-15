import { QuickBookFlow } from "../../components/booking/QuickBookFlow";

/**
 * Manager booking on a customer's behalf (phone-in bookings) — the exact
 * same two-step quick flow the customer sees, just with the customer's
 * name and number typed in by the manager. The profile is found or
 * created from the phone, same as a self-service booking.
 */
export default function ManagerNewBookingPage() {
  return <QuickBookFlow mode="manager" />;
}
