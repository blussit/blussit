/** A booking status in the customer's words — the raw values read like
 *  internal jargon ("pending" is a confirmed booking waiting for a captain). */
const CUSTOMER_STATUS: Record<string, string> = {
  awaiting_payment: "Payment Pending",
  pending: "Confirmed",
  rescheduled: "Rescheduled",
  assigned: "Captain Assigned",
  captain_on_the_way: "On The Way",
  service_started: "In Progress",
  completed: "Completed",
  cancelled: "Cancelled",
};

export const customerStatusLabel = (status?: string | null): string | undefined => (status ? CUSTOMER_STATUS[status] : undefined);
