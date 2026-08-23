.PHONY: help fmt lint validate test security license sbom build package e2e clean release attest

# Portfolio common vocabulary (narwhal#162/#161): help/fmt/lint/validate/test/security/
# license/sbom/build/package/e2e/clean/release/attest. Every target below either wraps a
# real existing check/script or says plainly what isn't implemented yet and why — no
# target reports success without actually doing the thing its name promises.

help:
	@echo "Targets:"
	@echo "  lint      shellcheck + yamllint (same as CI)"
	@echo "  validate  Vagrantfile syntax + GitOps YAML parse (same as CI)"
	@echo "  test      static regression suite; prints the live-cluster commands"
	@echo "  security  lightweight hardcoded-password/private-key grep (see Makefile for scope)"
	@echo "  license   re-resolve scripts/airgap/lib/component-licenses.tsv against upstream"
	@echo "  sbom      generate the CycloneDX SBOM for an assembled airgap bundle"
	@echo "  package   assemble the air-gapped install bundle (see scripts/airgap/README.md)"
	@echo "  e2e       full (non-static) regression suite — needs a live cluster"
	@echo "  clean     remove local generated airgap bundle output (does not touch a running VM)"
	@echo "  release   how a release is cut (this repo tags; it does not push tags for you)"
	@echo "  attest    current signing/provenance status"
	@echo "  fmt       no auto-formatter is configured for shell/YAML in this repo — see lint"

fmt:
	@echo "No formatter is configured for shell/YAML in this repo. 'make lint' catches style" \
	     "issues (shellcheck, yamllint, the 2-space indent check) but does not auto-fix them."

lint:
	find scripts/ -name '*.sh' -print0 | xargs -0 shellcheck --severity=warning
	yamllint -d '{extends: default, rules: {line-length: {max: 200}, truthy: disable}}' gitops/apps/*.yaml gitops/resources/*.yaml

validate:
	ruby -c Vagrantfile
	@for f in gitops/apps/*.yaml gitops/resources/*.yaml; do \
		yq eval '.' "$$f" > /dev/null && echo "OK: $$f" || echo "FAIL: $$f"; \
	done

test:
	./scripts/test/regression-check-kakao.sh --static
	@echo "Live-cluster half (needs a running cluster):"
	@echo "  vagrant ssh master-1 -c 'bash /home/vagrant/scripts/test/verify-cluster.sh'"
	@echo "  vagrant ssh master-1 -c 'bash /home/vagrant/scripts/test/test-sso.sh'"

# There is no automated dependency/image vulnerability scan or CI job for it yet
# (tracked in narwhal#52/#53) — this target is deliberately narrow: it checks for a
# literal `password: "<value>"` and private-key blocks hardcoded into scripts or
# manifests, the specific case CLAUDE.md's Guardrails section forbids. Deliberately
# does NOT match on the word "secret" alone — `existingSecret`/`secretName`-style
# fields reference a K8s Secret OBJECT by name (the normal, correct GitOps pattern
# throughout this repo), not a literal credential value, and matching them would make
# this check chronically noisy on legitimate manifests. Not a substitute for a real
# secret scanner.
security:
	@bad=0; \
	for f in $$(find scripts/ gitops/ -type f \( -name '*.sh' -o -name '*.yaml' -o -name '*.yml' \)); do \
		if grep -inE '(password|passwd)\s*[:=]\s*["'"'"'][^"'"'"'$$][^"'"'"']{3,}["'"'"']|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY' "$$f" > /dev/null; then \
			echo "possible hardcoded credential: $$f"; \
			bad=1; \
		fi; \
	done; \
	if [ "$$bad" -eq 0 ]; then echo "no obvious hardcoded credentials found"; fi; \
	exit "$$bad"

license:
	./scripts/airgap/lib/refresh-component-licenses.sh --check

# 08-generate-sbom.sh describes an already-assembled bundle directory — there is no
# default bundle in a plain checkout, so this only works after 'make package' (or the
# equivalent scripts/airgap/0[1-7]-*.sh sequence) has produced one.
sbom:
	@if [ -z "$(BUNDLE)" ]; then \
		echo "usage: make sbom BUNDLE=./narwhal-airgap-bundle-<arch>"; \
		echo "(run 'make package' first, or scripts/airgap/README.md's manual sequence)"; \
		exit 1; \
	fi
	./scripts/airgap/08-generate-sbom.sh --bundle "$(BUNDLE)"

# No single build artifact — this repo provisions infrastructure, it does not compile a
# binary. 'package' is the closest equivalent: the multi-step air-gapped install bundle
# assembly (scripts/airgap/01 through 09). See scripts/airgap/README.md for the full,
# environment-dependent sequence — not reducible to one Makefile recipe without a target
# environment to assemble for.
build package:
	@echo "narwhal has no single compiled artifact. The bundle-assembly pipeline is" \
	     "documented in scripts/airgap/README.md (01-generate-image-list.sh through" \
	     "09-verify-bundle-completeness.sh) and needs a target environment to run against."

e2e:
	./scripts/test/regression-check-kakao.sh

clean:
	rm -rf narwhal-airgap-bundle-*

# This target intentionally does not push a tag — that is a decision the repo owner
# makes explicitly, not something a Makefile target should do on its own.
release:
	@echo "Releases are cut by pushing a 'v*' tag (see .github/workflows/release.yml)," \
	     "which extracts CHANGELOG.md's matching section as release notes. This target" \
	     "does not push tags for you."

# SECURITY.md already states this plainly; this target exists so 'make attest' answers
# rather than 404s, not to add a second copy of the same claim to drift out of sync.
attest:
	@echo "Current status: releases are not signed and carry no build provenance" \
	     "attestation yet. See SECURITY.md and narwhal#161 (portfolio-wide provenance" \
	     "contract) for the target state."
