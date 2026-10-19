"use client";

import { ChangePassword } from "@/components/ChangePassword";
import { PageHeader } from "@/components/ui";
import { useAuth } from "@/lib/auth";

export default function AccountPage() {
  const { me } = useAuth();
  return (
    <>
      <PageHeader title="Akun saya" description={`${me.full_name} · ${me.username ?? me.email}`} />
      <div className="max-w-xl">
        <ChangePassword forced={me.must_change_password} />
      </div>
    </>
  );
}
