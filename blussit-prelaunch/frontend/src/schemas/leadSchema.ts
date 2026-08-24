import { z } from "zod";

// Accepts common Indian mobile formats (+91, 91, or bare 10-digit
// starting 6-9) and normalizes to a plain 10-digit string.
const INDIAN_MOBILE_REGEX = /^(?:\+?91[-\s]?)?([6-9]\d{9})$/;

export const leadFormSchema = z.object({
  name: z
    .string()
    .trim()
    .min(2, "Please enter your full name.")
    .max(80, "That name looks too long."),

  mobile: z
    .string()
    .trim()
    .min(1, "Mobile number is required.")
    .refine((val) => INDIAN_MOBILE_REGEX.test(val.replace(/[\s-]/g, "")), {
      message: "Enter a valid 10-digit Indian mobile number.",
    })
    .transform((val) => val.replace(/[\s-]/g, "").match(INDIAN_MOBILE_REGEX)![1]),

  area: z
    .string()
    .trim()
    .min(2, "Tell us your area or locality.")
    .max(120, "That area name looks too long."),

  interestedServices: z
    .array(
      z.enum([
        "Waterless Wash",
        "Normal Wash",
        "Interior Clean",
        "Deep Cleaning",
        "Polish & Waxing",
        "Monthly Plan",
        "Yearly Plan",
        "Not Sure Yet",
      ])
    )
    .min(1, "Choose at least one service."),
});

export type LeadFormValues = z.infer<typeof leadFormSchema>;
