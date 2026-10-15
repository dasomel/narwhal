# Cosign image signing — one-time human setup (narwhal#35)

> **Status:** the key pair was provisioned on 2026-10-15. The secrets live on `dasomel/narwhal`
> and the public key is committed in this repo (`scripts/airgap/keys/cosign.pub`) and inlined in
> `gitops/resources/kyverno-policies.yaml`. Sections 1-4 are kept as the rotation procedure.

`.github/workflows/portal-docker-publish.yml`'s `sign` job signs every portal image (tags
`portal/v*.*.*`) so
narwhal's Kyverno `verify-image-signatures` ClusterPolicy and its air-gap preflight gate
(`scripts/airgap/10-verify-image-signatures.sh`) can verify them. Both consume a **static Cosign
key pair** — not Sigstore keyless/Fulcio/Rekor — because narwhal's cluster is air-gapped and must
verify without reaching the public Rekor transparency log (see narwhal's
`docs/common/supply-chain-policy.md` §6 for the design decision).

The workflow only *references* the key material via GitHub Actions secrets; no private key is stored
in this repo. This doc is the exact procedure a human runs once to provision it.

## 1. Generate the key pair

Run this locally, **not** in CI — the private key must never touch a CI log or artifact.

```bash
cosign generate-key-pair
```

This prompts for a password and writes two files to the current directory:

- `cosign.key` — the password-encrypted private key. **Never commit this. Never paste it
  anywhere but the secret command below.**
- `cosign.pub` — the public key. Not sensitive; narwhal commits it (see step 4).

If you need a non-interactive password (e.g. scripting the whole setup in one go), export
`COSIGN_PASSWORD` first — `cosign generate-key-pair` reads it instead of prompting:

```bash
export COSIGN_PASSWORD='<a strong password, from a password manager, not this shell history>'
cosign generate-key-pair
```

## 2. Create the two GitHub Actions secrets

The `sign` job reads exactly these two secret names — they must match exactly:

```bash
gh secret set COSIGN_PRIVATE_KEY --repo dasomel/narwhal < cosign.key
gh secret set COSIGN_PASSWORD --repo dasomel/narwhal
# ^ paste the same password used in step 1 when prompted (or pipe it: echo "$COSIGN_PASSWORD" | gh secret set COSIGN_PASSWORD --repo dasomel/narwhal)
```

Both are required. `cosign.key` from `generate-key-pair` is password-encrypted by default, and
`cosign sign` only decrypts it non-interactively when `COSIGN_PASSWORD` is set in the
environment — without it, the signing step would block on a stdin prompt that doesn't exist on a
GitHub Actions runner. The `sign` job checks for both before attempting to sign, and **fails the
workflow outright** if either is missing on a real `portal/v*.*.*` release tag (it only warns-and-skips
on an ad-hoc `workflow_dispatch` build).

## 3. Delete the private key locally

```bash
rm -f cosign.key
```

Nothing else needs it once the secret is set. If you used a shell with history, also scrub
`COSIGN_PASSWORD` from your shell history / unset it from the session.

## 4. Commit the public key to this repo

The public key lives in narwhal in two places (no cross-repo copy anymore, since `portal/` is part
of this repo):

- `scripts/airgap/keys/cosign.pub` — file form for the air-gap bundle; pass
  `--key scripts/airgap/keys/cosign.pub` to `scripts/airgap/10-verify-image-signatures.sh`.
- `gitops/resources/kyverno-policies.yaml`'s `verify-image-signatures` ClusterPolicy — the PEM is
  **inlined** in both `publicKeys:` blocks (signature attestor and CycloneDX attestation attestor),
  because Kyverno takes key material inline, not as a file path.

Verification never depends on a live network call (the reason for a static key instead of
keyless/Rekor), so the key is a versioned file both consumers already have offline. Both copies are
updated by hand per rotation; `scripts/test/lib/check-kyverno-signed-image-policy.py` (R74/R75c)
fails if the inlined PEM differs from `scripts/airgap/keys/cosign.pub`.

## 5. Verify

After the secrets are set, push a `portal/v*.*.*` tag (or re-run the workflow via `workflow_dispatch` to
sanity-check the non-mandatory path) and confirm the `sign` job's "Sign image digest" step
succeeds. To manually verify a signed image once `cosign.pub` is committed:

```bash
# Online (Rekor reachable): the CI signature is in the public Rekor log, so tlog is checked.
cosign verify --key scripts/airgap/keys/cosign.pub ghcr.io/dasomel/narwhal-portal@sha256:<digest>

# Offline / air-gapped (no Rekor access): skip the tlog lookup, the static key alone is trusted.
cosign verify --key scripts/airgap/keys/cosign.pub --insecure-ignore-tlog=true \
  ghcr.io/dasomel/narwhal-portal@sha256:<digest>
```

**D-REKOR.** The `sign` job keeps uploading to the public Rekor log (the image is public, so
online consumers get transparency). Every *offline* verifier skips the tlog instead: Kyverno's key
attestors set `rekor.ignoreTlog: true` and `ctlog.ignoreSCT: true`, and offline `cosign verify`
uses `--insecure-ignore-tlog=true`. Cost: offline verification relies solely on the static key (no
transparency-log cross-check). Escape hatch: drop the ignore flags if the cluster ever gains Rekor
reachability. `scripts/test/lib/check-kyverno-signed-image-policy.py` (R74/R75d) enforces the flags.

### Local sanity check (cosign v3 gotcha)

To check a freshly generated pair offline, sign a blob without a transparency log. On cosign v3,
`--tlog-upload=false` alone is rejected by the default signing config, so pass
`--use-signing-config=false` with it and write the output via `--bundle`:

```bash
cosign sign-blob --key cosign.key --use-signing-config=false --tlog-upload=false \
  --bundle blob.bundle some-file
```

## Key rotation

Repeat steps 1–4 with a new key pair, update both `publicKeys:` blocks in
`gitops/resources/kyverno-policies.yaml` and `scripts/airgap/keys/cosign.pub`, and overwrite the two
GitHub secrets (`gh secret set` on an existing name replaces it). Old signatures made with the
retired key stop verifying the moment narwhal's key is swapped — there is no overlap window in
this design, so coordinate the narwhal-side update to land before or alongside the secret swap.
