import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Megaphone, Save } from "lucide-react";
import { adminHomepageConfigApi } from "../../api/admin";
import { catalogApi, comboOfferApi } from "../../api/catalog";
import { Button, Card, CardBody, Input, PageLoader, Select } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";

export default function AdminHomepageSettingsPage() {
  const queryClient = useQueryClient();
  const { data: config, isLoading } = useQuery({ queryKey: ["homepage-config"], queryFn: adminHomepageConfigApi.get });
  const { data: services } = useQuery({ queryKey: ["admin-services-for-homepage"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  const { data: combos } = useQuery({ queryKey: ["admin-combos-for-homepage"], queryFn: () => comboOfferApi.list(true) });

  const [form, setForm] = useState({
    hero_badge_text: "",
    hero_headline: "",
    hero_subtext: "",
    featured_service_id: "",
    banner_active: false,
    banner_text: "",
  });
  const [featuredCombos, setFeaturedCombos] = useState<string[]>([]);
  const [initialized, setInitialized] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (config && !initialized) {
      setForm({
        hero_badge_text: config.hero_badge_text,
        hero_headline: config.hero_headline,
        hero_subtext: config.hero_subtext,
        featured_service_id: config.featured_service_id || "",
        banner_active: config.banner_active,
        banner_text: config.banner_text,
      });
      setFeaturedCombos(config.featured_combo_ids);
      setInitialized(true);
    }
  }, [config, initialized]);

  const saveMutation = useMutation({
    mutationFn: () =>
      adminHomepageConfigApi.set({
        hero_badge_text: form.hero_badge_text,
        hero_headline: form.hero_headline,
        hero_subtext: form.hero_subtext,
        featured_service_id: form.featured_service_id || null,
        featured_combo_ids: featuredCombos,
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

  const toggleCombo = (id: string) => setFeaturedCombos((prev) => (prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]));

  if (isLoading) return <PageLoader />;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Homepage settings</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Everything shown on the landing page — headline, featured pricing, promo banner — lives here, not in code.
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
          <h2 className="font-semibold text-[var(--color-text-primary)]">Featured pricing</h2>
          <p className="text-sm text-[var(--color-text-secondary)]">
            Which service's regular/first-time price shows in the hero and pricing cards. Leave unset to use the first active
            service automatically.
          </p>
          <Select
            label="Featured service"
            value={form.featured_service_id}
            onChange={(e) => setForm({ ...form, featured_service_id: e.target.value })}
          >
            <option value="">Auto (first active service)</option>
            {(services?.data || []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} — ₹{s.discounted_price ?? s.price} / ₹{s.price}
              </option>
            ))}
          </Select>
        </CardBody>
      </Card>

      <Card>
        <CardBody className="space-y-3">
          <h2 className="font-semibold text-[var(--color-text-primary)]">Featured combo offers</h2>
          <p className="text-sm text-[var(--color-text-secondary)]">Pick which combos get their own highlighted section on the homepage.</p>
          <div className="flex flex-wrap gap-2">
            {(combos || []).length === 0 && <p className="text-sm text-[var(--color-text-secondary)]">No active combo offers yet.</p>}
            {(combos || []).map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => toggleCombo(c.id)}
                className={`rounded-full border px-3.5 py-1.5 text-sm font-medium ${
                  featuredCombos.includes(c.id) ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-gray-200 text-gray-600"
                }`}
              >
                {c.name}
              </button>
            ))}
          </div>
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
