import { QuickBookFlow } from "../../components/booking/QuickBookFlow";

/**
 * A job the manager already did himself (phone-in or walk-in): the same
 * vehicle/service picker as a booking, then who/where/when as it happened.
 * Saved directly as done — no captain, no photos — with a switch for the
 * one "service done" WhatsApp to the customer.
 */
export default function ManagerLogJobPage() {
  return <QuickBookFlow mode="manager-log" />;
}
