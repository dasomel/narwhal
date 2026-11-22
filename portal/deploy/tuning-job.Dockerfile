# narwhal-tuning job image (issue #55, item 2)
#
# k8s-job-runner.ts (runHostJob) runs this image as a privileged, hostPID+hostNetwork
# pod that nsenters into the host's namespaces to apply allowlisted node-tuning
# operations (see src/lib/tuning-commands.ts). It previously ran `apk add --no-cache
# util-linux` at container start on every single job execution — fetching tooling from
# the network at runtime, unpinned, defeating the digest pin already enforced on
# TUNING_JOB_IMAGE (see getTuningImage() in k8s-job-runner.ts). This Dockerfile bakes
# nsenter (from util-linux) in at BUILD time instead, so no package fetch happens at
# job runtime.
#
# D1: Pin the registry index for repeatable base selection; refresh manually (no Docker bot).
# Cost: no automatic base updates; escape: resolve the same tag with crane digest on refresh.
FROM alpine:3.21@sha256:bc21487101ae47172509130ee1092cc273a831968d64b617776d1f69c81e42ab

# util-linux provides /usr/bin/nsenter. `apk add util-linux` alone floats to whatever
# the mirror serves at BUILD time (still better than the old runtime fetch, but not
# fully pinned) — resolve the exact version before shipping this to production:
#
#   docker run --rm alpine:3.21 sh -c 'apk update >/dev/null && apk info util-linux'
#
# then replace the line below with `util-linux=<resolved-version>` (this repo's build
# pipeline has no live registry/network access to resolve that version from this pass,
# so it is left unpinned here rather than asserting an unverified number).
RUN apk add --no-cache util-linux \
    && nsenter --version

# No shell entrypoint beyond what k8s-job-runner.ts supplies via the pod's
# `command`/`args` (command: ["/bin/sh", "-c"], args: ["nsenter ... sh -c <script>"]).
