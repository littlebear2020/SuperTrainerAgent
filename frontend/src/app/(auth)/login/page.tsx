"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FloatingBlobsBackground } from "@/components/workspace/floating-blobs-background";
import { useAuth } from "@/core/auth/AuthProvider";
import { resolveNextPath } from "@/core/auth/next-path";
import { parseAuthError } from "@/core/auth/types";
import { useI18n } from "@/core/i18n/hooks";
import { useGlassStyle } from "@/core/theme/glass";
import { cn } from "@/lib/utils";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function LoginPage() {
  const { t } = useI18n();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isAuthenticated } = useAuth();
  const { theme, resolvedTheme } = useTheme();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [isLogin, setIsLogin] = useState(true);
  const [error, setError] = useState("");
  // DSN-003a（2026-09-14）：受控校验——移除 required 原生气泡，空值/格式错出
  // 站内样式红字 + 描边（submit 前手动校验）。
  const [fieldErrors, setFieldErrors] = useState<{
    email?: string;
    password?: string;
  }>({});
  const [loading, setLoading] = useState(false);

  // Get next parameter for validated redirect
  const nextParam = searchParams.get("next");
  const redirectPath = resolveNextPath(nextParam);

  // Redirect if already authenticated (client-side, post-login)
  useEffect(() => {
    if (isAuthenticated) {
      router.push(redirectPath);
    }
  }, [isAuthenticated, redirectPath, router]);

  // Redirect to setup if the system has no users yet
  useEffect(() => {
    let cancelled = false;

    void fetch("/api/v1/auth/setup-status")
      .then((r) => r.json())
      .then((data: { needs_setup?: boolean }) => {
        if (!cancelled && data.needs_setup) {
          router.push("/setup");
        }
      })
      .catch(() => {
        // Ignore errors; user stays on login page
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    // DSN-003a：受控校验（无浏览器原生 required 气泡）
    const minLen = isLogin ? 6 : 8;
    const errs: { email?: string; password?: string } = {};
    if (!email.trim()) {
      errs.email = t.auth.emailRequired;
    } else if (!EMAIL_RE.test(email.trim())) {
      errs.email = t.auth.emailInvalid;
    }
    if (!password) {
      errs.password = t.auth.passwordRequired;
    } else if (password.length < minLen) {
      errs.password = t.auth.passwordMin(minLen);
    }
    setFieldErrors(errs);
    if (errs.email || errs.password) return;

    setLoading(true);

    try {
      const endpoint = isLogin
        ? "/api/v1/auth/login/local"
        : "/api/v1/auth/register";
      const body = isLogin
        ? `username=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}`
        : JSON.stringify({ email, password, invite_code: inviteCode || null });

      const headers: HeadersInit = isLogin
        ? { "Content-Type": "application/x-www-form-urlencoded" }
        : { "Content-Type": "application/json" };

      const res = await fetch(endpoint, {
        method: "POST",
        headers,
        body,
        credentials: "include", // Important: include HttpOnly cookie
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      // Both login and register set a cookie — redirect to workspace
      router.push(redirectPath);
    } catch {
      setError(t.auth.networkError);
    } finally {
      setLoading(false);
    }
  };

  const { glassStyle, isDark } = useGlassStyle();

  return (
    <div className="flex min-h-screen items-center justify-center">
      <FloatingBlobsBackground />
      <div
        style={glassStyle()}
        className="interactive-glass relative z-10 w-full max-w-md space-y-6 rounded-3xl p-8"
      >
        <div className="relative z-[1] text-center">
          <h1
            className="text-3xl font-extrabold"
            style={{
              background: "linear-gradient(135deg, #6366f1, #ec4899)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
            }}
          >
            Trainer AI
          </h1>
          <p
            className={cn("mt-2", isDark ? "text-slate-400" : "text-slate-500")}
          >
            {isLogin ? t.auth.signInSubtitle : t.auth.signUpSubtitle}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="relative z-[1] space-y-2">
          <div className="flex flex-col space-y-1">
            <label
              htmlFor="email"
              className={cn(
                "text-sm font-medium",
                isDark ? "text-slate-300" : "text-slate-700",
              )}
            >
              {t.auth.email}
            </label>
            <Input
              id="email"
              type="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                if (fieldErrors.email)
                  setFieldErrors((f) => ({ ...f, email: undefined }));
              }}
              placeholder={t.auth.emailPlaceholder}
              aria-invalid={!!fieldErrors.email}
              className={cn(fieldErrors.email && "border-destructive")}
            />
            {fieldErrors.email && (
              <p className="text-xs text-red-500">{fieldErrors.email}</p>
            )}
          </div>
          <div className="flex flex-col space-y-1">
            <label
              htmlFor="password"
              className={cn(
                "text-sm font-medium",
                isDark ? "text-slate-300" : "text-slate-700",
              )}
            >
              {t.auth.password}
            </label>
            <Input
              id="password"
              type="password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                if (fieldErrors.password)
                  setFieldErrors((f) => ({ ...f, password: undefined }));
              }}
              placeholder={t.auth.passwordPlaceholder}
              aria-invalid={!!fieldErrors.password}
              className={cn(fieldErrors.password && "border-destructive")}
            />
            {fieldErrors.password && (
              <p className="text-xs text-red-500">{fieldErrors.password}</p>
            )}
          </div>

          {!isLogin && (
            <div className="flex flex-col space-y-1">
              <label
                htmlFor="inviteCode"
                className={cn(
                  "text-sm font-medium",
                  isDark ? "text-slate-300" : "text-slate-700",
                )}
              >
                {t.auth.inviteCode}
              </label>
              <Input
                id="inviteCode"
                type="text"
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                placeholder={t.auth.inviteCodePlaceholder}
              />
            </div>
          )}

          {error && <p className="text-sm text-red-500">{error}</p>}

          <Button
            type="submit"
            className="w-full bg-gradient-to-r from-indigo-500 to-purple-600 text-white hover:shadow-[0_0_20px_rgba(99,102,241,0.3)] hover:brightness-110"
            disabled={loading}
          >
            {loading
              ? t.auth.pleaseWait
              : isLogin
                ? t.auth.signIn
                : t.auth.signUp}
          </Button>
        </form>

        <div className="relative z-[1] text-center text-sm">
          <button
            type="button"
            onClick={() => {
              setIsLogin(!isLogin);
              setError("");
              setFieldErrors({});
            }}
            className="text-indigo-500 hover:underline"
          >
            {isLogin ? t.auth.toSignUp : t.auth.toSignIn}
          </button>
        </div>

        <div
          className={cn(
            "relative z-[1] text-center text-xs",
            isDark ? "text-slate-500" : "text-slate-400",
          )}
        >
          <Link href="/" className="hover:underline">
            {t.auth.backHome}
          </Link>
        </div>
      </div>
    </div>
  );
}
