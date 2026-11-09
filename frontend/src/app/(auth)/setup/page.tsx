"use client";

import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { FlickeringGrid } from "@/components/ui/flickering-grid";
import { Input } from "@/components/ui/input";
import { getCsrfHeaders } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { parseAuthError } from "@/core/auth/types";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

type SetupMode = "loading" | "init_admin" | "change_password";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function SetupPage() {
  const { t } = useI18n();
  const router = useRouter();
  const { user, isAuthenticated } = useAuth();
  const { theme, resolvedTheme } = useTheme();
  const [mode, setMode] = useState<SetupMode>("loading");

  // --- Shared state ---
  const [email, setEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  // DSN-003a（2026-09-14）：受控校验（去 required 原生气泡，站内红字 + 描边）
  const [fieldErrors, setFieldErrors] = useState<{
    email?: string;
    password?: string;
    confirmPassword?: string;
    currentPassword?: string;
  }>({});
  const [loading, setLoading] = useState(false);

  // --- Change-password mode only ---
  const [currentPassword, setCurrentPassword] = useState("");

  useEffect(() => {
    let cancelled = false;

    if (isAuthenticated && user?.needs_setup) {
      setMode("change_password");
    } else if (!isAuthenticated) {
      // Check if the system has no users yet
      void fetch("/api/v1/auth/setup-status")
        .then((r) => {
          if (r.status === 429) {
            // Rate limited — assume still needs setup; backend /initialize validates on submit
            return { needs_setup: true } as const;
          }
          return r.json() as Promise<{ needs_setup?: boolean }>;
        })
        .then((data: { needs_setup?: boolean }) => {
          if (cancelled) return;
          if (data.needs_setup) {
            setMode("init_admin");
          } else {
            // System already set up and user is not logged in — go to login
            router.push("/login");
          }
        })
        .catch(() => {
          // Network error (gateway unreachable, DNS failure, etc.)
          // Allow init attempt — backend /initialize will reject if system already initialized
          if (!cancelled) setMode("init_admin");
        });
    } else {
      // Authenticated but needs_setup is false — already set up
      router.push("/workspace");
    }

    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, user, router]);

  // ── Init-admin handler ─────────────────────────────────────────────
  const handleInitAdmin = async (e: React.SubmitEvent) => {
    e.preventDefault();
    setError("");

    // DSN-003a：受控校验
    const errs: typeof fieldErrors = {};
    if (!email.trim()) {
      errs.email = t.auth.emailRequired;
    } else if (!EMAIL_RE.test(email.trim())) {
      errs.email = t.auth.emailInvalid;
    }
    if (newPassword.length < 8) {
      errs.password = t.auth.passwordMin(8);
    }
    if (confirmPassword !== newPassword) {
      errs.confirmPassword = t.auth.passwordMismatch;
    }
    setFieldErrors(errs);
    if (errs.email || errs.password || errs.confirmPassword) return;

    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/initialize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          email,
          password: newPassword,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      router.push("/workspace");
    } catch {
      setError(t.auth.networkError);
    } finally {
      setLoading(false);
    }
  };

  // ── Change-password handler ────────────────────────────────────────
  const handleChangePassword = async (e: React.SubmitEvent) => {
    e.preventDefault();
    setError("");

    const errs: typeof fieldErrors = {};
    if (email && !EMAIL_RE.test(email.trim())) {
      errs.email = t.auth.emailInvalid;
    }
    if (!currentPassword) {
      errs.currentPassword = t.auth.passwordRequired;
    }
    if (newPassword.length < 8) {
      errs.password = t.auth.passwordMin(8);
    }
    if (confirmPassword !== newPassword) {
      errs.confirmPassword = t.auth.passwordMismatch;
    }
    setFieldErrors(errs);
    if (
      errs.email ||
      errs.password ||
      errs.confirmPassword ||
      errs.currentPassword
    )
      return;

    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/change-password", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getCsrfHeaders(),
        },
        credentials: "include",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
          new_email: email || undefined,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      router.push("/workspace");
    } catch {
      setError(t.auth.networkError);
    } finally {
      setLoading(false);
    }
  };

  const actualTheme = theme === "system" ? resolvedTheme : theme;

  const clearField = (key: keyof typeof fieldErrors) =>
    setFieldErrors((f) => (f[key] ? { ...f, [key]: undefined } : f));

  if (mode === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-muted-foreground text-sm">{t.auth.loading}</p>
      </div>
    );
  }

  // ── Admin initialization form ──────────────────────────────────────
  if (mode === "init_admin") {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center">
        <FlickeringGrid
          className="absolute inset-0 z-0 mask-[url(/images/deer.svg)] mask-size-[100vw] mask-center mask-no-repeat md:mask-size-[72vh]"
          squareSize={4}
          gridGap={4}
          color={actualTheme === "dark" ? "white" : "black"}
          maxOpacity={0.3}
          flickerChance={0.25}
        />
        <div className="border-border/20 bg-background/5 w-full max-w-md space-y-6 rounded-3xl border p-8 backdrop-blur-sm">
          <div className="text-center">
            <h1
              className="text-3xl font-bold"
              style={{
                background: "linear-gradient(135deg, #6366f1, #ec4899)",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
              }}
            >
              Trainer AI
            </h1>
            <p className="text-muted-foreground mt-2">
              {t.auth.createAdminSubtitle}
            </p>
            <p className="text-muted-foreground mt-1 text-xs">
              {t.auth.createAdminHint}
            </p>
          </div>
          <form onSubmit={handleInitAdmin} className="space-y-2">
            <div className="flex flex-col space-y-1">
              <label htmlFor="email" className="text-sm font-medium">
                {t.auth.email}
              </label>
              <Input
                id="email"
                type="email"
                placeholder={t.auth.emailPlaceholder}
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  clearField("email");
                }}
                aria-invalid={!!fieldErrors.email}
                className={cn(fieldErrors.email && "border-destructive")}
              />
              {fieldErrors.email && (
                <p className="text-xs text-red-500">{fieldErrors.email}</p>
              )}
            </div>
            <div className="flex flex-col space-y-1">
              <label htmlFor="password" className="text-sm font-medium">
                {t.auth.password}
              </label>
              <Input
                id="password"
                type="password"
                placeholder={t.auth.passwordMin8Placeholder}
                value={newPassword}
                onChange={(e) => {
                  setNewPassword(e.target.value);
                  clearField("password");
                }}
                aria-invalid={!!fieldErrors.password}
                className={cn(fieldErrors.password && "border-destructive")}
              />
              {fieldErrors.password && (
                <p className="text-xs text-red-500">{fieldErrors.password}</p>
              )}
            </div>
            <div className="flex flex-col space-y-1">
              <label htmlFor="confirmPassword" className="text-sm font-medium">
                {t.auth.confirmPassword}
              </label>
              <Input
                id="confirmPassword"
                type="password"
                placeholder={t.auth.confirmPlaceholder}
                value={confirmPassword}
                onChange={(e) => {
                  setConfirmPassword(e.target.value);
                  clearField("confirmPassword");
                }}
                aria-invalid={!!fieldErrors.confirmPassword}
                className={cn(
                  fieldErrors.confirmPassword && "border-destructive",
                )}
              />
              {fieldErrors.confirmPassword && (
                <p className="text-xs text-red-500">
                  {fieldErrors.confirmPassword}
                </p>
              )}
            </div>
            {error && <p className="ms-1 text-sm text-red-500">{error}</p>}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? t.auth.creating : t.auth.createAdminAccount}
            </Button>
          </form>
        </div>
      </div>
    );
  }

  // ── Change-password form (needs_setup after login) ─────────────────
  return (
    <div className="bg-background flex min-h-screen items-center justify-center">
      <FlickeringGrid
        className="absolute inset-0 z-0 mask-[url(/images/deer.svg)] mask-size-[100vw] mask-center mask-no-repeat md:mask-size-[72vh]"
        squareSize={4}
        gridGap={4}
        color={actualTheme === "dark" ? "white" : "black"}
        maxOpacity={0.3}
        flickerChance={0.25}
      />
      <div className="border-border/20 bg-background/5 w-full max-w-md space-y-6 rounded-3xl border p-8 backdrop-blur-sm">
        <div className="text-center">
          <h1 className="font-serif text-3xl">Trainer AI</h1>
          <p className="text-muted-foreground mt-2">
            {t.auth.completeSetupSubtitle}
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            {t.auth.completeSetupHint}
          </p>
        </div>
        <form onSubmit={handleChangePassword} className="space-y-4">
          <div className="flex flex-col space-y-1">
            <Input
              type="email"
              placeholder={t.auth.yourEmailPlaceholder}
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                clearField("email");
              }}
              aria-invalid={!!fieldErrors.email}
              className={cn(fieldErrors.email && "border-destructive")}
            />
            {fieldErrors.email && (
              <p className="text-xs text-red-500">{fieldErrors.email}</p>
            )}
          </div>
          <div className="flex flex-col space-y-1">
            <Input
              type="password"
              placeholder={t.auth.currentPasswordPlaceholder}
              value={currentPassword}
              onChange={(e) => {
                setCurrentPassword(e.target.value);
                clearField("currentPassword");
              }}
              aria-invalid={!!fieldErrors.currentPassword}
              className={cn(
                fieldErrors.currentPassword && "border-destructive",
              )}
            />
            {fieldErrors.currentPassword && (
              <p className="text-xs text-red-500">
                {fieldErrors.currentPassword}
              </p>
            )}
          </div>
          <div className="flex flex-col space-y-1">
            <Input
              type="password"
              placeholder={t.auth.newPasswordPlaceholder}
              value={newPassword}
              onChange={(e) => {
                setNewPassword(e.target.value);
                clearField("password");
              }}
              aria-invalid={!!fieldErrors.password}
              className={cn(fieldErrors.password && "border-destructive")}
            />
            {fieldErrors.password && (
              <p className="text-xs text-red-500">{fieldErrors.password}</p>
            )}
          </div>
          <div className="flex flex-col space-y-1">
            <Input
              type="password"
              placeholder={t.auth.confirmNewPlaceholder}
              value={confirmPassword}
              onChange={(e) => {
                setConfirmPassword(e.target.value);
                clearField("confirmPassword");
              }}
              aria-invalid={!!fieldErrors.confirmPassword}
              className={cn(
                fieldErrors.confirmPassword && "border-destructive",
              )}
            />
            {fieldErrors.confirmPassword && (
              <p className="text-xs text-red-500">
                {fieldErrors.confirmPassword}
              </p>
            )}
          </div>
          {error && <p className="text-sm text-red-500">{error}</p>}
          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? t.auth.settingUp : t.auth.completeSetup}
          </Button>
        </form>
      </div>
    </div>
  );
}
