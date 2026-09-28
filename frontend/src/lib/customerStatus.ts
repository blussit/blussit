/** A booking status in the customer's words — the raw values read like
 *  internal jargon ("pending" is a confirmed booking waiting for a captain). */
const CUSTOMER_STATUS: Record<string, string> = {
  awaiting_payment: "Payment pending",
  pending: "Confirmed",
  rescheduled: "Rescheduled",
  assigned: "Captain assigned",
  captain_on_the_way: "On the way",
  service_started: "In progress",
  completed: "Completed",
  cancelled: "Cancelled",
};

export const customerStatusLabel = (status?: string | null): string | undefined => (status ? CUSTOMER_STATUS[status] : undefined);
