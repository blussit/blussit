import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Megaphone, Save } from "lucide-react";
import { adminHomepageConfigApi } from "../../api/admin";
import { Button, Card, CardBody, Input, PageLoader } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";

export default function AdminHomepageSettingsPage() {
  const queryClient = useQueryClient();
  const { data: config, isLoading } = useQuery({ queryKey: ["homepage-config"], queryFn: adminHomepageConfigApi.get });

  const [form, setForm] = useState({
    hero_badge_text: "",
    hero_headline: "",
    hero_subtext: "",
    banner_active: false,
    banner_text: "",
  });
  const [initialized, setInitialized] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (config && !initialized) {
      setForm({
        hero_badge_text: config.hero_badge_text,
        hero_headline: config.hero_headline,
        hero_subtext: config.hero_subtext,
        banner_active: config.banner_active,
        banner_text: config.banner_text,
      });
      setInitialized(true);
    }
  }, [config, initialized]);

  const saveMutation = useMutation({
    mutationFn: () =>
      adminHomepageConfigApi.set({
        hero_badge_text: form.hero_badge_text,
        hero_headline: form.hero_headline,
        hero_subtext: form.hero_subtext,
        banner_active: form.banner_active,
        banner_text: form.banner_text,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["homepage-config"] });
      setSaved(true);
      setError("");
      setTimeout(() => setSaved(false), 2500);
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  if (isLoading) return <PageLoader />;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Homepage settings</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          The hero badge, headline, subtext and promo banner on the landing page live here — saved changes appear on the site immediately.
        </p>
      </div>

      <Card>
        <CardBody className="space-y-4">
          <h2 className="font-semibold text-[var(--color-text-primary)]">Hero section</h2>
          <Input label="Badge text" value={form.hero_badge_text} onChange={(e) => setForm({ ...form, hero_badge_text: e.target.value })} />
          <Input label="Headline" value={form.hero_headline} onChange={(e) => setForm({ ...form, hero_headline: e.target.value })} />
          <Input label="Subtext" value={form.hero_subtext} onChange={(e) => setForm({ ...form, hero_subtext: e.target.value })} />
        </CardBody>
      </Card>

      <Card>
        <CardBody className="space-y-3">
          <h2 className="flex items-center gap-2 font-semibold text-[var(--color-text-primary)]">
            <Megaphone className="h-4 w-4" /> Promo banner
          </h2>
          <label className="flex items-center gap-2 text-sm text-[var(--color-text-primary)]">
            <input type="checkbox" checked={form.banner_active} onChange={(e) => setForm({ ...form, banner_active: e.target.checked })} />
            Show a banner strip above the hero
          </label>
          <Input
            label="Banner text"
            value={form.banner_text}
            onChange={(e) => setForm({ ...form, banner_text: e.target.value })}
            placeholder="e.g. Diwali special — 20% off all combos this week"
            disabled={!form.banner_active}
          />
        </CardBody>
      </Card>

      {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
      {saved && <p className="text-sm text-[var(--color-success)]">Homepage settings updated.</p>}
      <Button isLoading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
        <Save className="h-4 w-4" /> Save homepage settings
      </Button>
    </div>
  );
}
