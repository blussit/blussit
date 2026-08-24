import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { Button, Input } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";

export default function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ full_name: "", email: "", phone: "", password: "" });
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!form.email && !form.phone) {
      setError("Please provide an email or phone number");
      return;
    }
    setIsLoading(true);
    try {
      await register({
        full_name: form.full_name,
        email: form.email || undefined,
        phone: form.phone || undefined,
        password: form.password,
      });
      navigate("/app");
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-surface)] px-4 py-10">
      <div className="w-full max-w-sm">
        <Link to="/" className="mb-8 flex items-center justify-center gap-2 font-display text-lg font-bold text-[var(--color-primary)]">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--color-primary)] text-white">
            <Sparkles className="h-5 w-5" />
          </span>
          CLEAN<span className="text-[var(--color-secondary)]">RIDE</span>
        </Link>

        <div className="rounded-2xl border border-gray-100 bg-white p-8 shadow-[var(--shadow-soft)]">
          <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">Create your account</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Book your first doorstep service in minutes.</p>

          <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
            <Input label="Full name" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
            <Input label="Email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            <Input label="Phone number" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            <Input
              label="Password"
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              hint="At least 8 characters"
              required
            />
            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            <Button type="submit" className="w-full" isLoading={isLoading}>
              Create account
            </Button>
          </form>
        </div>

        <p className="mt-6 text-center text-sm text-[var(--color-text-secondary)]">
          Already have an account?{" "}
          <Link to="/login" className="font-medium text-[var(--color-primary)] hover:underline">
            Log in
          </Link>
        </p>
      </div>
    </div>
  );
}
