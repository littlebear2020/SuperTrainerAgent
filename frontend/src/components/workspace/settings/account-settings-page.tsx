"use client";

import { LogOutIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { parseAuthError } from "@/core/auth/types";

import { SettingsSection } from "./settings-section";

interface InviteConfig {
  enabled: boolean;
  code_masked: string;
  max_uses: number | null;
  current_uses: number;
  expires_at: string | null;
}

export function AccountSettingsPage() {
  const { user, logout } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Invite code state
  const isAdmin = user?.system_role === "admin";
  const [inviteEnabled, setInviteEnabled] = useState(false);
  const [inviteCode, setInviteCode] = useState("");
  const [inviteMaxUses, setInviteMaxUses] = useState("");
  const [inviteExpiresAt, setInviteExpiresAt] = useState("");
  const [inviteCurrentUses, setInviteCurrentUses] = useState(0);
  const [inviteMasked, setInviteMasked] = useState("");
  const [inviteLoading, setInviteLoading] = useState(false);
  const [inviteMessage, setInviteMessage] = useState("");
  const [inviteError, setInviteError] = useState("");

  useEffect(() => {
    if (!isAdmin) return;
    fetch("/api/v1/auth/invite-config")
      .then((r) => r.json())
      .then((data: InviteConfig) => {
        setInviteEnabled(data.enabled);
        setInviteMasked(data.code_masked);
        setInviteMaxUses(data.max_uses?.toString() ?? "");
        setInviteCurrentUses(data.current_uses);
        if (data.expires_at) {
          setInviteExpiresAt(
            new Date(data.expires_at).toISOString().slice(0, 16),
          );
        }
      })
      .catch((error) => {
        console.error("Failed to fetch invite config:", error);
      });
  }, [isAdmin]);

  const handleSaveInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    setInviteError("");
    setInviteMessage("");
    setInviteLoading(true);

    try {
      const res = await fetch("/api/v1/auth/invite-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...getCsrfHeaders() },
        body: JSON.stringify({
          enabled: inviteEnabled,
          code: inviteCode,
          max_uses: inviteMaxUses ? parseInt(inviteMaxUses, 10) : null,
          expires_at: inviteExpiresAt
            ? new Date(inviteExpiresAt).toISOString()
            : null,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setInviteError(authError.message);
        return;
      }

      const data: InviteConfig = await res.json();
      setInviteEnabled(data.enabled);
      setInviteMasked(data.code_masked);
      setInviteCurrentUses(data.current_uses);
      setInviteCode("");
      setInviteMessage("Invite code settings saved");
    } catch {
      setInviteError("Network error. Please try again.");
    } finally {
      setInviteLoading(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setMessage("");

    if (newPassword !== confirmPassword) {
      setError("New passwords do not match");
      return;
    }
    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/change-password", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getCsrfHeaders(),
        },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      setMessage("Password changed successfully");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-8">
      <SettingsSection title="Profile">
        <div className="space-y-2">
          <div className="grid grid-cols-[max-content_max-content] items-center gap-4">
            <span className="text-muted-foreground text-sm">Email</span>
            <span className="text-sm font-medium">{user?.email ?? "—"}</span>
            <span className="text-muted-foreground text-sm">Role</span>
            <span className="text-sm font-medium capitalize">
              {user?.system_role ?? "—"}
            </span>
          </div>
        </div>
      </SettingsSection>

      {isAdmin && (
        <SettingsSection
          title="Invite Code"
          description="Configure registration invite code. When enabled, new users must provide the code to register."
        >
          <form onSubmit={handleSaveInvite} className="max-w-sm space-y-3">
            <div className="flex items-center gap-2">
              <input
                id="invite-enabled"
                type="checkbox"
                checked={inviteEnabled}
                onChange={(e) => setInviteEnabled(e.target.checked)}
                className="size-4"
              />
              <label htmlFor="invite-enabled" className="text-sm font-medium">
                Require invite code for registration
              </label>
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium">
                Invite Code{" "}
                {inviteMasked && (
                  <span className="text-muted-foreground">
                    (current: {inviteMasked})
                  </span>
                )}
              </label>
              <Input
                type="text"
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                placeholder="Enter new invite code (min 6 chars)"
                minLength={6}
              />
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium">
                Max Uses (empty = unlimited)
              </label>
              <Input
                type="number"
                value={inviteMaxUses}
                onChange={(e) => setInviteMaxUses(e.target.value)}
                placeholder="Unlimited"
                min={1}
              />
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium">
                Expires At (empty = never)
              </label>
              <Input
                type="datetime-local"
                value={inviteExpiresAt}
                onChange={(e) => setInviteExpiresAt(e.target.value)}
              />
            </div>

            <p className="text-muted-foreground text-xs">
              Used: {inviteCurrentUses}
              {inviteMaxUses ? ` / ${inviteMaxUses}` : ""}
            </p>

            {inviteError && (
              <p className="text-sm text-red-500">{inviteError}</p>
            )}
            {inviteMessage && (
              <p className="text-sm text-green-500">{inviteMessage}</p>
            )}
            <Button
              type="submit"
              variant="outline"
              size="sm"
              disabled={inviteLoading}
            >
              {inviteLoading ? "Saving..." : "Save Invite Settings"}
            </Button>
          </form>
        </SettingsSection>
      )}

      <SettingsSection
        title="Change Password"
        description="Update your account password."
      >
        <form onSubmit={handleChangePassword} className="max-w-sm space-y-3">
          <Input
            type="password"
            placeholder="Current password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            required
          />
          <Input
            type="password"
            placeholder="New password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
            minLength={8}
          />
          <Input
            type="password"
            placeholder="Confirm new password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
            minLength={8}
          />
          {error && <p className="text-sm text-red-500">{error}</p>}
          {message && <p className="text-sm text-green-500">{message}</p>}
          <Button type="submit" variant="outline" size="sm" disabled={loading}>
            {loading ? "Updating..." : "Update Password"}
          </Button>
        </form>
      </SettingsSection>

      <SettingsSection title="" description="">
        <Button
          variant="destructive"
          size="sm"
          onClick={logout}
          className="gap-2"
        >
          <LogOutIcon className="size-4" />
          Sign Out
        </Button>
      </SettingsSection>
    </div>
  );
}
