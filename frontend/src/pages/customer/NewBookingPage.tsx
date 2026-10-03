import { QuickBookFlow } from "../../components/booking/QuickBookFlow";

/**
 * /app/book — the same booking page as /book, framed by the customer
 * portal: name, phone and saved addresses prefilled, any matching pass
 * applied by the server. Deep links: ?service=<slug>, ?subscription=<id>
 * (book with a plan), ?repeat=<bookingId> ("book again"), ?type=<vehicleTypeId>
 * (that car type preselected).
 */
export default function NewBookingPage() {
  return <QuickBookFlow mode="customer" layout="app" />;
}
