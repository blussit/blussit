export type InterestedService =
  | "Waterless Wash"
  | "Normal Wash"
  | "Interior Clean"
  | "Deep Cleaning"
  | "Polish & Waxing"
  | "Monthly Plan"
  | "Yearly Plan"
  | "Not Sure Yet";

export interface LeadPayload {
  name: string;
  mobile: string;
  area: string;
  city: string;
  interestedServices: InterestedService[];
  source: string;
}

export interface LeadResponse {
  success: boolean;
  leadId?: string;
  message: string;
}
