import { QuickBookFlow } from "../../components/booking/QuickBookFlow";

/**
 * /app/book — the same two-step quick booking as the public page, with the
 * signed-in customer's name, phone and saved addresses prefilled and any
 * matching pass applied automatically by the server. Supports
 * ?repeat=<bookingId> ("book again").
 */
export default function NewBookingPage() {
  return <QuickBookFlow mode="customer" />;
}
