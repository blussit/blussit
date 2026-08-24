import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { Button, Input } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { roleHomePath } from "../../lib/roleHome";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setIsLoading(true);
    try {
      const user = await login(identifier, password);
      navigate(roleHomePath(user.role));
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-surface)] px-4">
      <div className="w-full max-w-sm">
        <Link to="/" className="mb-8 flex items-center justify-center gap-2 font-display text-lg font-bold text-[var(--color-primary)]">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--color-primary)] text-white">
            <Sparkles className="h-5 w-5" />
          </span>
          CLEAN<span className="text-[var(--color-secondary)]">RIDE</span>
        </Link>

        <div className="rounded-2xl border border-gray-100 bg-white p-8 shadow-[var(--shadow-soft)]">
          <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">Welcome back</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Log in to manage your bookings.</p>

          <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
            <Input label="Email or phone number" value={identifier} onChange={(e) => setIdentifier(e.target.value)} required />
            <Input label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            <div className="flex justify-end">
              <Link to="/forgot-password" className="text-xs font-medium text-[var(--color-primary)] hover:underline">
                Forgot password?
              </Link>
            </div>
            <Button type="submit" className="w-full" isLoading={isLoading}>
              Log in
            </Button>
          </form>
        </div>

        <p className="mt-6 text-center text-sm text-[var(--color-text-secondary)]">
          New here?{" "}
          <Link to="/register" className="font-medium text-[var(--color-primary)] hover:underline">
            Create an account
          </Link>
        </p>
      </div>
    </div>
  );
}
