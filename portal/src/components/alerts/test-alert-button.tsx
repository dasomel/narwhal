"use client"
import { useMutation } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { useT } from "@/lib/i18n-client"

export function TestAlertButton() {
  const t = useT()
  const mutation = useMutation({
    mutationFn: async () => {
      const res = await fetch("/api/alerts/test", { method: "POST" })
      if (!res.ok) throw new Error("Test failed")
    },
  })
  return <div className="space-y-2">
    <Button variant="outline" disabled={mutation.isPending} onClick={() => mutation.mutate()}>{t("notifications.test")}</Button>
    <p role="status" className="text-sm text-muted-foreground">{mutation.isSuccess ? t("notifications.testAccepted") : mutation.isError ? t("notifications.testFailed") : t("notifications.testHint")}</p>
  </div>
}
