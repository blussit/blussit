import type { LeadPayload, LeadResponse } from "@/types/lead";

// Web3Forms lets this stay a pure frontend project — no backend to host.
// Get a free access key at https://web3forms.com (takes ~30 seconds,
// just needs the email you want submissions delivered to) and put it
// in frontend/.env as VITE_WEB3FORMS_ACCESS_KEY.
const WEB3FORMS_ENDPOINT = "https://api.web3forms.com/submit";
const ACCESS_KEY = import.meta.env.VITE_WEB3FORMS_ACCESS_KEY;

export async function submitLead(payload: LeadPayload): Promise<LeadResponse> {
  if (!ACCESS_KEY) {
    throw new Error(
      "Form is not configured yet. Add VITE_WEB3FORMS_ACCESS_KEY in .env (see README)."
    );
  }

  const response = await fetch(WEB3FORMS_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      access_key: ACCESS_KEY,
      subject: `New BLUSSIT lead — ${payload.name} (${payload.area})`,
      from_name: "BLUSSIT Pre-Launch Website",
      // Custom fields — these show up as rows in the email Web3Forms sends you.
      Name: payload.name,
      Mobile: payload.mobile,
      Area: payload.area,
      City: payload.city,
      "Interested Services": payload.interestedServices.join(", "),
      Source: payload.source,
      // Honeypot field Web3Forms uses for spam filtering — must stay empty.
      botcheck: "",
    }),
  });

  const data = await response.json().catch(() => null);

  if (!response.ok || !data?.success) {
    throw new Error(data?.message ?? "Something went wrong. Please try again.");
  }

  return {
    success: true,
    message: data.message ?? "Lead captured.",
  };
}
