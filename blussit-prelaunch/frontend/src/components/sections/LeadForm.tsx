import { useEffect, useState } from "react";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { motion, AnimatePresence } from "framer-motion";
import { CheckCircle2, Loader2 } from "lucide-react";
import { leadFormSchema, type LeadFormValues } from "@/schemas/leadSchema";
import { submitLead } from "@/services/api/leadsApi";
import { siteConfig } from "@/config/site";
import { track } from "@/services/analytics";
import { Reveal } from "@/components/ui/Reveal";

const serviceOptions = [
  "Waterless Wash",
  "Normal Wash",
  "Interior Clean",
  "Deep Cleaning",
  "Polish & Waxing",
  "Monthly Plan",
  "Yearly Plan",
  "Not Sure Yet",
] as const;

export function LeadForm() {
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [hasStarted, setHasStarted] = useState(false);

  const {
    register,
    handleSubmit,
    control,
    formState: { errors, isSubmitting },
  } = useForm<LeadFormValues>({
    resolver: zodResolver(leadFormSchema),
    defaultValues: { interestedServices: [] },
  });

  useEffect(() => {
    if (hasStarted) track("form_started");
  }, [hasStarted]);

  const onSubmit = async (values: LeadFormValues) => {
    setSubmitError(null);
    try {
      await submitLead({
        ...values,
        city: siteConfig.launchCity,
        source: "prelaunch_website",
      });
      track("form_completed");
      setSubmitted(true);
    } catch (err) {
      setSubmitError(
        err instanceof Error ? err.message : "Something went wrong. Please try again."
      );
    }
  };

  return (
    <section id="early-access" className="bg-white py-20">
      <div className="mx-auto max-w-2xl px-6 md:px-10">
        <Reveal className="text-center">
          <div className="text-[11px] font-extrabold uppercase tracking-[0.2em] text-goldDeep">
            Don't miss launch day
          </div>
          <h2 className="mt-2 text-[2.1rem] font-extrabold tracking-[-0.03em] text-ink sm:text-4xl">
            Be first to know.
          </h2>
          <p className="mt-3 text-[15px] text-muted">
            Leave your details and we'll keep your launch reminder ready.
          </p>
        </Reveal>

        <div className="relative mt-10 rounded-3xl border border-border bg-cream p-6 shadow-[0_20px_60px_rgba(0,0,0,0.04)] sm:p-8">
          <AnimatePresence mode="wait">
            {submitted ? (
              <motion.div
                key="success"
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
                className="flex flex-col items-center py-10 text-center"
              >
                <span className="flex h-14 w-14 items-center justify-center rounded-full bg-goldSoft text-goldDeep">
                  <CheckCircle2 size={28} />
                </span>
                <h3 className="mt-6 text-2xl font-bold text-ink">You're on the list 🎉</h3>
                <p className="mt-3 max-w-sm text-[15px] text-muted">
                  BLUSSIT is coming to {siteConfig.launchCity} on {siteConfig.launchDateLabel} at{" "}
                  {siteConfig.launchTimeLabel}. We'll keep you posted.
                </p>
                <p className="mt-1 text-sm font-semibold text-ink/70">See you at the doorstep.</p>
              </motion.div>
            ) : (
              <motion.form
                key="form"
                initial={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onSubmit={handleSubmit(onSubmit)}
                onChange={() => setHasStarted(true)}
                noValidate
                className="grid gap-4 sm:grid-cols-2"
              >
                <div>
                  <label htmlFor="name" className="text-sm font-semibold text-ink">
                    Name
                  </label>
                  <input
                    id="name"
                    type="text"
                    autoComplete="name"
                    placeholder="Your full name"
                    {...register("name")}
                    className="mt-2 w-full rounded-xl border border-border bg-white px-4 py-3.5 text-[15px] outline-none focus:border-gold focus:ring-2 focus:ring-gold/20"
                  />
                  {errors.name && (
                    <p className="mt-1.5 text-sm text-red-500">{errors.name.message}</p>
                  )}
                </div>

                <div>
                  <label htmlFor="mobile" className="text-sm font-semibold text-ink">
                    Mobile Number
                  </label>
                  <input
                    id="mobile"
                    type="tel"
                    inputMode="numeric"
                    autoComplete="tel"
                    placeholder="98765 43210"
                    {...register("mobile")}
                    className="mt-2 w-full rounded-xl border border-border bg-white px-4 py-3.5 text-[15px] outline-none focus:border-gold focus:ring-2 focus:ring-gold/20"
                  />
                  {errors.mobile && (
                    <p className="mt-1.5 text-sm text-red-500">{errors.mobile.message}</p>
                  )}
                </div>

                <div className="sm:col-span-2">
                  <label htmlFor="area" className="text-sm font-semibold text-ink">
                    Area / Locality
                  </label>
                  <input
                    id="area"
                    type="text"
                    placeholder="Vijay Nagar, Indore"
                    {...register("area")}
                    className="mt-2 w-full rounded-xl border border-border bg-white px-4 py-3.5 text-[15px] outline-none focus:border-gold focus:ring-2 focus:ring-gold/20"
                  />
                  {errors.area && (
                    <p className="mt-1.5 text-sm text-red-500">{errors.area.message}</p>
                  )}
                </div>

                <fieldset className="sm:col-span-2 border-0 p-0 m-0">
                  <legend className="text-sm font-semibold text-ink">
                    What service are you interested in?
                  </legend>
                  <Controller
                    name="interestedServices"
                    control={control}
                    render={({ field }) => (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {serviceOptions.map((service) => {
                          const checked = field.value?.includes(service);
                          return (
                            <button
                              type="button"
                              key={service}
                              aria-pressed={checked}
                              onClick={() => {
                                const next = checked
                                  ? field.value.filter((s) => s !== service)
                                  : [...(field.value ?? []), service];
                                field.onChange(next);
                              }}
                              className={`rounded-full border px-4 py-2 text-sm font-medium transition-colors ${
                                checked
                                  ? "border-gold bg-goldSoft text-goldDeep"
                                  : "border-border bg-white text-ink/70"
                              }`}
                            >
                              {service}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  />
                  {errors.interestedServices && (
                    <p className="mt-1.5 text-sm text-red-500">
                      {errors.interestedServices.message}
                    </p>
                  )}
                </fieldset>

                {submitError && (
                  <p className="sm:col-span-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600">
                    {submitError}
                  </p>
                )}

                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="sm:col-span-2 flex w-full items-center justify-center gap-2 rounded-xl bg-dark px-6 py-4 text-[15px] font-bold text-white transition-colors hover:bg-dark/90 disabled:opacity-70"
                >
                  {isSubmitting ? (
                    <>
                      <Loader2 size={18} className="animate-spin" /> Submitting…
                    </>
                  ) : (
                    siteConfig.formCta.toUpperCase() + " →"
                  )}
                </button>
              </motion.form>
            )}
          </AnimatePresence>
        </div>
      </div>
    </section>
  );
}
