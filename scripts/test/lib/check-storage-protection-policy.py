#!/usr/bin/env python3
"""Assert statically visible storage, backup, and export facts documented in storage-protection-policy.md.

Guards documented CURRENT facts so manifests and scripts do not silently drift from the policy.
Each assertion cites the corresponding line in docs/common/storage-protection-policy.md.
Facts labeled PROPOSED are intentionally not asserted.
"""

import argparse
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[3]
DOC_REL_PATH = "docs/common/storage-protection-policy.md"


class PolicyChecker:
  """Stateful checker that collects failures instead of aborting on first mismatch."""

  def __init__(self):
    self.failures: List[str] = []
    self.warnings: List[str] = []

  def fail(self, doc_line: str, message: str) -> None:
    self.failures.append(f"FAIL: {doc_line} {message}")

  def warn(self, message: str) -> None:
    self.warnings.append(f"WARN: {message}")

  def check_storage_policy(
    self,
    root: Path = ROOT,
    file_overrides: Optional[Dict[str, str]] = None,
  ) -> Tuple[int, int, int]:
    """Run all assertions against files under root.

    Args:
      root: repository root
      file_overrides: optional dict mapping relative paths to in-memory text content,
        used by mutation checks to avoid filesystem copies.

    Returns (sources_count, files_scanned, assertions_count).
    """
    ovr = file_overrides
    sources_count = 0
    files_scanned = 0
    assertions_count = 0

    def read_file(rel_path: str) -> Optional[str]:
      """Read from override dict if given, else filesystem.

      When file_overrides is provided it is a complete snapshot: absent keys mean
      the file does not exist (never fall through to disk).
      """
      if ovr is not None:
        return ovr.get(rel_path)
      p = root / rel_path
      if p.is_file():
        return p.read_text(encoding="utf-8")
      return None

    # 1. Verify storage protection policy doc itself exists and records current baseline.
    doc_text = read_file(DOC_REL_PATH)
    if doc_text is None:
      self.fail(DOC_REL_PATH, f"storage policy document missing at {root / DOC_REL_PATH}")
      return sources_count, files_scanned, assertions_count
    sources_count += 1
    files_scanned += 1

    # Doc presence checks
    for snippet in (
      "scripts/cluster/04-addons.sh",
      "nfs-csi",
      "nfs.csi.k8s.io",
      "scripts/cluster/01-nfs-server.sh",
      "/srv/nfs/k8s",
      "sync,root_squash",
      "gitops/resources/narwhal-db.yaml",
      "s3://cnpg-backup/narwhal-db",
      'retentionPolicy: "7d"',
      "gitops/charts/narwhal-apps/templates/velero.yaml",
      "uploaderType: kopia",
      "snapshotsEnabled: false",
      "daily-full",
    ):
      if snippet not in doc_text:
        self.fail(DOC_REL_PATH, f"policy document missing expected baseline declaration snippet: {snippet!r}")
      assertions_count += 1

    # 2. Check StorageClass nfs-csi in scripts/cluster/04-addons.sh (Doc Line 9)
    addons_rel = "scripts/cluster/04-addons.sh"
    addons_text = read_file(addons_rel)
    if addons_text is None:
      self.fail(f"{DOC_REL_PATH}:9", f"addons script missing at {root / addons_rel}")
    else:
      sources_count += 1
      files_scanned += 1

      sc_match = re.search(
        r"(apiVersion:\s*storage\.k8s\.io/v1\s*\nkind:\s*StorageClass.*?\n)EOF",
        addons_text,
        re.DOTALL,
      )
      if not sc_match:
        self.fail(f"{DOC_REL_PATH}:9", f"StorageClass manifest heredoc missing in {addons_rel}")
      else:
        try:
          sc_doc = yaml.safe_load(sc_match.group(1))
        except yaml.YAMLError as exc:
          self.fail(f"{DOC_REL_PATH}:9", f"Failed to parse StorageClass manifest in {addons_rel}: {exc}")
          sc_doc = None

        if sc_doc is not None:
          # Doc Line 9: StorageClass metadata.name is nfs-csi
          if sc_doc.get("metadata", {}).get("name") != "nfs-csi":
            self.fail(f"{DOC_REL_PATH}:9", f"expected StorageClass metadata.name='nfs-csi', got {sc_doc.get('metadata', {}).get('name')!r}")
          assertions_count += 1

          # Doc Line 9: provisioner is nfs.csi.k8s.io
          if sc_doc.get("provisioner") != "nfs.csi.k8s.io":
            self.fail(f"{DOC_REL_PATH}:9", f"expected StorageClass provisioner='nfs.csi.k8s.io', got {sc_doc.get('provisioner')!r}")
          assertions_count += 1

          # Doc Line 9: parameters use ${NFS_SERVER_IP} and ${NFS_SHARE_PATH}
          sc_params = sc_doc.get("parameters", {})
          if sc_params.get("server") != "${NFS_SERVER_IP}":
            self.fail(f"{DOC_REL_PATH}:9", f"expected StorageClass parameter server='${{NFS_SERVER_IP}}', got {sc_params.get('server')!r}")
          assertions_count += 1
          if sc_params.get("share") != "${NFS_SHARE_PATH}":
            self.fail(f"{DOC_REL_PATH}:9", f"expected StorageClass parameter share='${{NFS_SHARE_PATH}}', got {sc_params.get('share')!r}")
          assertions_count += 1

          # Doc Line 9: reclaimPolicy Retain
          if sc_doc.get("reclaimPolicy") != "Retain":
            self.fail(f"{DOC_REL_PATH}:9", f"expected StorageClass reclaimPolicy='Retain', got {sc_doc.get('reclaimPolicy')!r}")
          assertions_count += 1

          # Doc Line 9: volumeBindingMode Immediate
          if sc_doc.get("volumeBindingMode") != "Immediate":
            self.fail(f"{DOC_REL_PATH}:9", f"expected StorageClass volumeBindingMode='Immediate', got {sc_doc.get('volumeBindingMode')!r}")
          assertions_count += 1

          # Doc Line 9: allowVolumeExpansion not enabled (omitted or false)
          if sc_doc.get("allowVolumeExpansion") is True:
            self.fail(f"{DOC_REL_PATH}:9", "expected StorageClass allowVolumeExpansion to be omitted/false, got True")
          assertions_count += 1

    # 3. Check NFS server setup in scripts/cluster/01-nfs-server.sh (Doc Line 9)
    nfs_rel = "scripts/cluster/01-nfs-server.sh"
    nfs_text = read_file(nfs_rel)
    if nfs_text is None:
      self.fail(f"{DOC_REL_PATH}:9", f"NFS server script missing at {root / nfs_rel}")
    else:
      sources_count += 1
      files_scanned += 1

      # Doc Line 9: default share path is /srv/nfs/k8s
      share_match = re.search(r'NFS_SHARE_PATH=[\'\"${]*NFS_SHARE_PATH:-([^}\"\'\n]+)', nfs_text)
      if not share_match or share_match.group(1).strip() != "/srv/nfs/k8s":
        actual_share = share_match.group(1).strip() if share_match else "none"
        self.fail(f"{DOC_REL_PATH}:9", f"expected default NFS_SHARE_PATH='/srv/nfs/k8s', got {actual_share!r}")
      assertions_count += 1

      # Doc Line 9: sync,root_squash exports configured
      exports_options = re.findall(r"\$\{NFS_SHARE_PATH\}\s+\S+\(([^)]+)\)", nfs_text)
      if not exports_options:
        self.fail(f"{DOC_REL_PATH}:9", f"no export options declared in {nfs_rel}")
      for opt in exports_options:
        opt_set = set(x.strip() for x in opt.split(","))
        if "sync" not in opt_set:
          self.fail(f"{DOC_REL_PATH}:9", f"NFS export options missing 'sync': {opt}")
        assertions_count += 1
        if "root_squash" not in opt_set:
          self.fail(f"{DOC_REL_PATH}:9", f"NFS export options missing 'root_squash': {opt}")
        assertions_count += 1

    # F2: Vagrantfile validation — missing source is FAIL, not silent skip.
    vagrant_rel = "Vagrantfile"
    vagrant_text = read_file(vagrant_rel)
    if vagrant_text is None:
      self.fail(f"{DOC_REL_PATH}:9", f"Vagrantfile missing at {root / vagrant_rel}")
    else:
      sources_count += 1
      files_scanned += 1
      if not re.search(r'NFS_SHARE_PATH\s*=\s*"/srv/nfs/k8s"', vagrant_text):
        self.fail(f"{DOC_REL_PATH}:9", "Vagrantfile NFS_SHARE_PATH does not match '/srv/nfs/k8s'")
      assertions_count += 1

    # 4. Check absence of local PV / static PersistentVolume (Doc Line 10, Line 83)
    # F3: Extend to indented Helm-template manifests and shell heredocs.
    scan_dirs = [root / "scripts", root / "gitops/resources", root / "gitops/charts/narwhal-apps/templates"]
    source_candidates: List[Path] = []
    for sdir in scan_dirs:
      if sdir.is_dir():
        source_candidates.extend(sorted(f for f in sdir.rglob("*") if f.is_file()))

    if not source_candidates:
      self.fail(f"{DOC_REL_PATH}:10", f"no source files found in scan directories {scan_dirs}")

    # F3: PV/SC regex now accepts optional leading whitespace for indented Helm templates and heredocs.
    pv_re = re.compile(r"(?m)^\s*kind:\s*PersistentVolume\s*$")
    local_sc_re = re.compile(r"(?m)^\s*provisioner:\s*(kubernetes\.io/no-provisioner|rancher\.io/local-path)\s*$")

    for source_file in source_candidates:
      files_scanned += 1
      source_rel = source_file.relative_to(root).as_posix()
      if ovr is not None and source_rel in ovr:
        source_text = ovr[source_rel]
      else:
        source_bytes = source_file.read_bytes()
        if b"\0" in source_bytes:
          continue
        source_text = source_bytes.decode("utf-8", errors="ignore")
      if pv_re.search(source_text):
        self.fail(f"{DOC_REL_PATH}:10", f"unexpected static PersistentVolume found in {source_rel}")
      if local_sc_re.search(source_text):
        self.fail(f"{DOC_REL_PATH}:10", f"unexpected local StorageClass provisioner found in {source_rel}")
      assertions_count += 1

    sources_count += len(scan_dirs)

    # 5. Check CNPG Cluster and ScheduledBackup across GitOps YAML (Doc Line 11, Line 13)
    db_rel = "gitops/resources/narwhal-db.yaml"
    db_text = read_file(db_rel)
    if db_text is None:
      self.fail(f"{DOC_REL_PATH}:11", f"CNPG manifest missing at {root / db_rel}")
    else:
      sources_count += 1
      files_scanned += 1

      try:
        db_docs = list(yaml.safe_load_all(db_text))
      except yaml.YAMLError as exc:
        self.fail(f"{DOC_REL_PATH}:11", f"failed to parse {db_rel}: {exc}")
        db_docs = []

      cnpg_clusters = [
        d for d in db_docs
        if isinstance(d, dict) and d.get("kind") == "Cluster" and d.get("metadata", {}).get("name") == "narwhal-db"
      ]
      if len(cnpg_clusters) != 1:
        self.fail(f"{DOC_REL_PATH}:11", f"expected exactly one Cluster 'narwhal-db' in {db_rel}, found {len(cnpg_clusters)}")
      else:
        cluster = cnpg_clusters[0]

        # Doc Line 11: narwhal-db 2 instances
        if cluster.get("spec", {}).get("instances") != 2:
          self.fail(f"{DOC_REL_PATH}:11", f"expected CNPG instances=2, got {cluster.get('spec', {}).get('instances')}")
        assertions_count += 1

        # Doc Line 11: storageClass: nfs-csi
        if cluster.get("spec", {}).get("storage", {}).get("storageClass") != "nfs-csi":
          self.fail(f"{DOC_REL_PATH}:11", f"expected CNPG storageClass='nfs-csi', got {cluster.get('spec', {}).get('storage', {}).get('storageClass')!r}")
        assertions_count += 1

        # Doc Line 11: S3 destinationPath: s3://cnpg-backup/narwhal-db
        backup_spec = cluster.get("spec", {}).get("backup", {})
        barman = backup_spec.get("barmanObjectStore", {})
        if barman.get("destinationPath") != "s3://cnpg-backup/narwhal-db":
          self.fail(f"{DOC_REL_PATH}:11", f"expected CNPG destinationPath='s3://cnpg-backup/narwhal-db', got {barman.get('destinationPath')!r}")
        assertions_count += 1

        # Doc Line 11: WAL 및 data gzip 압축
        if barman.get("wal", {}).get("compression") != "gzip":
          self.fail(f"{DOC_REL_PATH}:11", f"expected CNPG wal.compression='gzip', got {barman.get('wal', {}).get('compression')!r}")
        assertions_count += 1
        if barman.get("data", {}).get("compression") != "gzip":
          self.fail(f"{DOC_REL_PATH}:11", f"expected CNPG data.compression='gzip', got {barman.get('data', {}).get('compression')!r}")
        assertions_count += 1

        # Doc Line 11: retentionPolicy: "7d"
        if backup_spec.get("retentionPolicy") != "7d":
          self.fail(f"{DOC_REL_PATH}:11", f"expected CNPG retentionPolicy='7d', got {backup_spec.get('retentionPolicy')!r}")
        assertions_count += 1

        # Doc Line 11: SeaweedFS in same cluster dependency
        endpoint = barman.get("endpointURL", "")
        if "seaweedfs-s3.storage.svc.cluster.local:8333" not in endpoint:
          self.fail(f"{DOC_REL_PATH}:11", f"expected CNPG endpointURL to target SeaweedFS in same cluster, got {endpoint!r}")
        assertions_count += 1

    gitops_yaml = sorted(
      f for f in (root / "gitops").rglob("*")
      if f.is_file() and f.suffix.lower() in (".yaml", ".yml")
      and "templates" not in f.relative_to(root / "gitops").parts
    )
    scheduled_backups = []
    for yaml_file in gitops_yaml:
      yaml_rel = yaml_file.relative_to(root).as_posix()
      yaml_text = ovr.get(yaml_rel) if ovr is not None and yaml_rel in ovr else yaml_file.read_text(encoding="utf-8", errors="ignore")
      files_scanned += 1
      try:
        yaml_docs = yaml.safe_load_all(yaml_text)
        scheduled_backups.extend(
          (yaml_rel, d) for d in yaml_docs
          if isinstance(d, dict) and d.get("kind") == "ScheduledBackup"
          and d.get("spec", {}).get("cluster", {}).get("name") == "narwhal-db"
        )
      except yaml.YAMLError as exc:
        self.fail(f"{DOC_REL_PATH}:13", f"failed to parse GitOps YAML {yaml_rel}: {exc}")
    matching_schedules = [
      (path, backup) for path, backup in scheduled_backups
      if backup.get("metadata", {}).get("name") == "narwhal-db-daily"
    ]
    if len(matching_schedules) != 1:
      self.fail(f"{DOC_REL_PATH}:13", f"expected one ScheduledBackup 'narwhal-db-daily' for cluster narwhal-db across GitOps YAML, found {len(matching_schedules)}")
    else:
      schedule_path, scheduled_backup = matching_schedules[0]
      if scheduled_backup.get("spec", {}).get("schedule") != "0 2 * * *":
        self.fail(f"{DOC_REL_PATH}:13", f"expected CNPG ScheduledBackup schedule='0 2 * * *' in {schedule_path}, got {scheduled_backup.get('spec', {}).get('schedule')!r}")
      assertions_count += 1
      if scheduled_backup.get("spec", {}).get("target") != "prefer-standby":
        self.fail(f"{DOC_REL_PATH}:13", f"expected CNPG ScheduledBackup target='prefer-standby' in {schedule_path}, got {scheduled_backup.get('spec', {}).get('target')!r}")
      assertions_count += 1

    # 6. Check SeaweedFS NFS-backed storageClass in gitops/charts/narwhal-apps/templates/seaweedfs.yaml (Doc Line 11)
    seaweed_rel = "gitops/charts/narwhal-apps/templates/seaweedfs.yaml"
    seaweed_text = read_file(seaweed_rel)
    if seaweed_text is None:
      self.fail(f"{DOC_REL_PATH}:11", f"SeaweedFS manifest missing at {root / seaweed_rel}")
    else:
      sources_count += 1
      files_scanned += 1

      try:
        seaweed_doc = yaml.safe_load(seaweed_text)
      except yaml.YAMLError as exc:
        self.fail(f"{DOC_REL_PATH}:11", f"failed to parse {seaweed_rel}: {exc}")
        seaweed_doc = None

      if seaweed_doc is not None:
        seaweed_values = seaweed_doc.get("spec", {}).get("source", {}).get("helm", {}).get("valuesObject", {})
        for comp in ("master", "volume", "filer"):
          sc_comp = seaweed_values.get(comp, {}).get("data", {}).get("storageClass")
          if sc_comp != "nfs-csi":
            self.fail(f"{DOC_REL_PATH}:11", f"expected SeaweedFS {comp}.data.storageClass='nfs-csi', got {sc_comp!r}")
          assertions_count += 1

    # 7. Check Velero backup configuration in gitops/charts/narwhal-apps/templates/velero.yaml (Doc Line 12, 13, 21)
    velero_rel = "gitops/charts/narwhal-apps/templates/velero.yaml"
    velero_text = read_file(velero_rel)
    if velero_text is None:
      self.fail(f"{DOC_REL_PATH}:12", f"Velero manifest missing at {root / velero_rel}")
    else:
      sources_count += 1
      files_scanned += 1

      try:
        velero_doc = yaml.safe_load(velero_text)
      except yaml.YAMLError as exc:
        self.fail(f"{DOC_REL_PATH}:12", f"failed to parse {velero_rel}: {exc}")
        velero_doc = None

      if velero_doc is not None:
        velero_values = velero_doc.get("spec", {}).get("source", {}).get("helm", {}).get("valuesObject", {})

        # Doc Line 12: backup location seaweedfs-s3.storage.svc.cluster.local:8333 bucket velero
        bsl_list = velero_values.get("configuration", {}).get("backupStorageLocation", [])
        if not bsl_list:
          self.fail(f"{DOC_REL_PATH}:12", f"no backupStorageLocation configured in {velero_rel}")
        else:
          bsl = bsl_list[0]
          if bsl.get("bucket") != "velero":
            self.fail(f"{DOC_REL_PATH}:12", f"expected Velero bucket='velero', got {bsl.get('bucket')!r}")
          assertions_count += 1
          bsl_s3 = bsl.get("config", {}).get("s3Url", "")
          if "seaweedfs-s3.storage.svc.cluster.local:8333" not in bsl_s3:
            self.fail(f"{DOC_REL_PATH}:12", f"expected Velero s3Url to target SeaweedFS, got {bsl_s3!r}")
          assertions_count += 1

        # Doc Line 12: uploaderType: kopia
        if velero_values.get("configuration", {}).get("uploaderType") != "kopia":
          self.fail(f"{DOC_REL_PATH}:12", f"expected Velero uploaderType='kopia', got {velero_values.get('configuration', {}).get('uploaderType')!r}")
        assertions_count += 1

        # Doc Line 12: defaultVolumesToFsBackup: true
        if velero_values.get("configuration", {}).get("defaultVolumesToFsBackup") is not True:
          self.fail(f"{DOC_REL_PATH}:12", f"expected Velero defaultVolumesToFsBackup=True, got {velero_values.get('configuration', {}).get('defaultVolumesToFsBackup')}")
        assertions_count += 1

        # Doc Line 12: snapshotsEnabled: false
        if velero_values.get("snapshotsEnabled") is not False:
          self.fail(f"{DOC_REL_PATH}:12", f"expected Velero snapshotsEnabled=False, got {velero_values.get('snapshotsEnabled')}")
        assertions_count += 1

        # Doc Line 12: node-agent 활성화 (deployNodeAgent: true)
        if velero_values.get("deployNodeAgent") is not True:
          self.fail(f"{DOC_REL_PATH}:12", f"expected Velero deployNodeAgent=True, got {velero_values.get('deployNodeAgent')}")
        assertions_count += 1

        # Doc Line 13: Schedules and TTLs
        expected_schedules = {
          "daily-full": {"schedule": "0 2 * * *", "ttl": "168h"},
          "daily-databases": {"schedule": "0 1 * * *", "ttl": "336h"},
          "daily-gitea": {"schedule": "0 3 * * *", "ttl": "336h"},
          "daily-harbor": {"schedule": "0 4 * * *", "ttl": "168h"},
          "daily-openbao": {"schedule": "0 5 * * *", "ttl": "336h"},
        }
        actual_schedules = velero_values.get("schedules", {})
        for sched_name, expected_cfg in expected_schedules.items():
          if sched_name not in actual_schedules:
            self.fail(f"{DOC_REL_PATH}:13", f"Velero schedule {sched_name!r} missing in {velero_rel}")
          else:
            actual_cfg = actual_schedules[sched_name]
            if actual_cfg.get("schedule") != expected_cfg["schedule"]:
              self.fail(f"{DOC_REL_PATH}:13", f"schedule {sched_name} expected schedule={expected_cfg['schedule']!r}, got {actual_cfg.get('schedule')!r}")
            assertions_count += 1
            actual_ttl = actual_cfg.get("template", {}).get("ttl")
            if actual_ttl != expected_cfg["ttl"]:
              self.fail(f"{DOC_REL_PATH}:13", f"schedule {sched_name} expected ttl={expected_cfg['ttl']!r}, got {actual_ttl!r}")
            assertions_count += 1
          assertions_count += 1

        # Doc Line 21: 애플리케이션 일관성 미구현/미검증 — Velero 설정에 workload별 quiesce hook 또는 공통 트랜잭션 경계 미선언
        if "hooks" in velero_values.get("configuration", {}) or "initHooks" in velero_values or "hooks" in velero_values:
          self.fail(f"{DOC_REL_PATH}:21", "unexpected global hooks declared in Velero configuration")
        assertions_count += 1
        for s_name, s_cfg in actual_schedules.items():
          if "hooks" in s_cfg or "hooks" in s_cfg.get("template", {}):
            self.fail(f"{DOC_REL_PATH}:21", f"unexpected hook declared in Velero schedule {s_name}")
          assertions_count += 1
        # F4: Use re.DOTALL so (pre|post).*hook matches across YAML line boundaries.
        if re.search(r"\b(pre|post)\b.*?\bhook\b", velero_text, re.IGNORECASE | re.DOTALL):
          self.fail(f"{DOC_REL_PATH}:21", f"unexpected quiesce hook declaration found in {velero_rel}")
        assertions_count += 1

    if assertions_count == 0 or files_scanned == 0 or sources_count == 0:
      self.fail("internal", f"zero evaluation: sources={sources_count}, files={files_scanned}, assertions={assertions_count}")

    return sources_count, files_scanned, assertions_count


def mutation_check(root: Path = ROOT) -> None:
  """Execute mutation checks against in-memory modifications to verify all guards fail when broken.

  F5: mutate in-memory text passed via file_overrides instead of copying whole trees.
  """
  print("Running mutation checks for check-storage-protection-policy...")

  # Pre-read the fixed files used by assertions; tree scans read remaining sources live.
  file_keys = [
    DOC_REL_PATH,
    "scripts/cluster/04-addons.sh",
    "scripts/cluster/01-nfs-server.sh",
    "Vagrantfile",
    "gitops/resources/narwhal-db.yaml",
    "gitops/resources/cnpg-backup.yaml",
    "gitops/charts/narwhal-apps/templates/seaweedfs.yaml",
    "gitops/charts/narwhal-apps/templates/velero.yaml",
  ]
  baseline: Dict[str, str] = {}
  for key in file_keys:
    p = root / key
    if p.is_file():
      baseline[key] = p.read_text(encoding="utf-8")

  def assert_mutation_rejected(
    overrides: Dict[str, str],
    description: str,
    error_substr: str = "",
  ) -> None:
    checker = PolicyChecker()
    checker.check_storage_policy(root, file_overrides=overrides)
    if not checker.failures:
      raise AssertionError(f"Mutation {description} was accepted unexpectedly!")
    if error_substr:
      if not any(error_substr in f for f in checker.failures):
        raise AssertionError(
          f"Mutation {description} raised unexpected errors: {checker.failures} (expected {error_substr!r})"
        )
    print(f"PASS: {description} was rejected")

  def test_text_mutation(
    target_file_rel: str,
    mutator_fn,
    description: str,
    error_substr: str = "",
  ) -> None:
    orig = baseline[target_file_rel]
    ovr = dict(baseline)
    ovr[target_file_rel] = mutator_fn(orig)
    assert_mutation_rejected(ovr, description, error_substr)

  # 1. Missing doc file
  ovr1 = dict(baseline)
  del ovr1[DOC_REL_PATH]
  assert_mutation_rejected(ovr1, "missing storage policy doc", "storage policy document missing")

  # 2. StorageClass reclaimPolicy mutated
  test_text_mutation(
    "scripts/cluster/04-addons.sh",
    lambda s: s.replace("reclaimPolicy: Retain", "reclaimPolicy: Delete"),
    "StorageClass reclaimPolicy=Delete",
    "reclaimPolicy='Retain'",
  )

  # 3. StorageClass volumeBindingMode mutated
  test_text_mutation(
    "scripts/cluster/04-addons.sh",
    lambda s: s.replace("volumeBindingMode: Immediate", "volumeBindingMode: WaitForFirstConsumer"),
    "StorageClass volumeBindingMode=WaitForFirstConsumer",
    "volumeBindingMode='Immediate'",
  )

  # 4. StorageClass allowVolumeExpansion mutated
  test_text_mutation(
    "scripts/cluster/04-addons.sh",
    lambda s: s.replace("volumeBindingMode: Immediate", "volumeBindingMode: Immediate\nallowVolumeExpansion: true"),
    "StorageClass allowVolumeExpansion=True",
    "allowVolumeExpansion to be omitted/false",
  )

  # 5. StorageClass provisioner mutated
  test_text_mutation(
    "scripts/cluster/04-addons.sh",
    lambda s: s.replace("provisioner: nfs.csi.k8s.io", "provisioner: kubernetes.io/nfs"),
    "StorageClass provisioner=kubernetes.io/nfs",
    "provisioner='nfs.csi.k8s.io'",
  )

  # 6. NFS server default share path mutated
  test_text_mutation(
    "scripts/cluster/01-nfs-server.sh",
    lambda s: s.replace("NFS_SHARE_PATH:-/srv/nfs/k8s", "NFS_SHARE_PATH:-/srv/nfs/other"),
    "NFS server share path mutated",
    "default NFS_SHARE_PATH='/srv/nfs/k8s'",
  )

  # 7. NFS export sync option dropped
  test_text_mutation(
    "scripts/cluster/01-nfs-server.sh",
    lambda s: s.replace("rw,sync,no_subtree_check,root_squash", "rw,async,no_subtree_check,root_squash"),
    "NFS export missing sync",
    "missing 'sync'",
  )

  # 8. NFS export root_squash option dropped
  test_text_mutation(
    "scripts/cluster/01-nfs-server.sh",
    lambda s: s.replace("rw,sync,no_subtree_check,root_squash", "rw,sync,no_subtree_check,no_root_squash"),
    "NFS export missing root_squash",
    "missing 'root_squash'",
  )

  # 9. PersistentVolume injected (in-memory: add a synthetic file to YAML scan dirs)
  # D1: PV injection can't use file_overrides for synthetic new files because the scan
  # uses rglob. Instead we create a single temp file and remove it immediately after.
  # This is the minimal filesystem touch needed for injection mutations.
  pv_file = root / "gitops/resources/local-pv.yaml"
  try:
    pv_file.write_text("apiVersion: v1\nkind: PersistentVolume\nmetadata:\n  name: local-disk\n")
    checker = PolicyChecker()
    checker.check_storage_policy(root, file_overrides=baseline)
    if not checker.failures or not any("unexpected static PersistentVolume" in f for f in checker.failures):
      raise AssertionError("Mutation static PersistentVolume added was accepted unexpectedly!")
    print("PASS: static PersistentVolume added was rejected")
  finally:
    if pv_file.is_file():
      pv_file.unlink()

  # F2: non-YAML source classes can still declare PVs and must be text-scanned.
  template_pv_file = root / "gitops/resources/storage-policy-pv-test.tpl"
  try:
    template_pv_file.write_text("kind: PersistentVolume\nmetadata:\n  name: local-template-disk\n")
    checker = PolicyChecker()
    checker.check_storage_policy(root, file_overrides=baseline)
    if not checker.failures or not any("unexpected static PersistentVolume" in f for f in checker.failures):
      raise AssertionError("Mutation PersistentVolume in .tpl source was accepted unexpectedly!")
    print("PASS: PersistentVolume in .tpl source was rejected")
  finally:
    if template_pv_file.is_file():
      template_pv_file.unlink()

  # 10. Local StorageClass provisioner injected
  sc_file = root / "gitops/resources/local-sc.yaml"
  try:
    sc_file.write_text("apiVersion: storage.k8s.io/v1\nkind: StorageClass\nmetadata:\n  name: local\nprovisioner: rancher.io/local-path\n")
    checker = PolicyChecker()
    checker.check_storage_policy(root, file_overrides=baseline)
    if not checker.failures or not any("unexpected local StorageClass provisioner" in f for f in checker.failures):
      raise AssertionError("Mutation local StorageClass provisioner added was accepted unexpectedly!")
    print("PASS: local StorageClass provisioner added was rejected")
  finally:
    if sc_file.is_file():
      sc_file.unlink()

  # 11. CNPG instances mutated
  test_text_mutation(
    "gitops/resources/narwhal-db.yaml",
    lambda s: s.replace("instances: 2", "instances: 1"),
    "CNPG instances=1",
    "expected CNPG instances=2",
  )

  # 12. CNPG storageClass mutated
  test_text_mutation(
    "gitops/resources/narwhal-db.yaml",
    lambda s: s.replace("storageClass: nfs-csi", "storageClass: standard"),
    "CNPG storageClass=standard",
    "storageClass='nfs-csi'",
  )

  # 13. CNPG destinationPath mutated
  test_text_mutation(
    "gitops/resources/narwhal-db.yaml",
    lambda s: s.replace("destinationPath: s3://cnpg-backup/narwhal-db", "destinationPath: s3://other-backup/narwhal-db"),
    "CNPG destinationPath mutated",
    "s3://cnpg-backup/narwhal-db",
  )

  # 14. CNPG retentionPolicy mutated
  test_text_mutation(
    "gitops/resources/narwhal-db.yaml",
    lambda s: s.replace('retentionPolicy: "7d"', 'retentionPolicy: "30d"'),
    "CNPG retentionPolicy='30d'",
    "retentionPolicy='7d'",
  )

  # 15. CNPG ScheduledBackup schedule drifted
  test_text_mutation(
    "gitops/resources/cnpg-backup.yaml",
    lambda s: s.replace('schedule: "0 2 * * *"', 'schedule: "0 3 * * *"'),
    "CNPG ScheduledBackup schedule=0 3 * * *",
    "expected CNPG ScheduledBackup schedule='0 2 * * *'",
  )

  # 16. SeaweedFS storageClass mutated
  test_text_mutation(
    "gitops/charts/narwhal-apps/templates/seaweedfs.yaml",
    lambda s: s.replace("storageClass: nfs-csi", "storageClass: hostpath"),
    "SeaweedFS storageClass=hostpath",
    "storageClass='nfs-csi'",
  )

  # 17. Velero uploaderType mutated
  test_text_mutation(
    "gitops/charts/narwhal-apps/templates/velero.yaml",
    lambda s: s.replace("uploaderType: kopia", "uploaderType: restic"),
    "Velero uploaderType=restic",
    "uploaderType='kopia'",
  )

  # 18. Velero snapshotsEnabled mutated
  test_text_mutation(
    "gitops/charts/narwhal-apps/templates/velero.yaml",
    lambda s: s.replace("snapshotsEnabled: false", "snapshotsEnabled: true"),
    "Velero snapshotsEnabled=True",
    "snapshotsEnabled=False",
  )

  # 19. Velero deployNodeAgent mutated
  test_text_mutation(
    "gitops/charts/narwhal-apps/templates/velero.yaml",
    lambda s: s.replace("deployNodeAgent: true", "deployNodeAgent: false"),
    "Velero deployNodeAgent=False",
    "deployNodeAgent=True",
  )

  # 20. Velero daily-full schedule mutated
  test_text_mutation(
    "gitops/charts/narwhal-apps/templates/velero.yaml",
    lambda s: s.replace('schedule: "0 2 * * *"', 'schedule: "0 3 * * *"'),
    "Velero daily-full schedule=0 3 * * *",
    "schedule='0 2 * * *'",
  )

  # 21. Velero daily-full ttl mutated
  test_text_mutation(
    "gitops/charts/narwhal-apps/templates/velero.yaml",
    lambda s: s.replace("ttl: 168h", "ttl: 24h"),
    "Velero daily-full ttl=24h",
    "ttl='168h'",
  )

  # 22. Velero hook injected
  test_text_mutation(
    "gitops/charts/narwhal-apps/templates/velero.yaml",
    lambda s: s.replace('schedule: "0 2 * * *"', 'schedule: "0 2 * * *"\n            hooks:\n              pre: []'),
    "Velero hook injected",
    "unexpected hook declared in Velero schedule",
  )


def main() -> int:
  parser = argparse.ArgumentParser(description="Check storage protection policy baseline against repo manifests")
  parser.add_argument("--mutation-verify", action="store_true", help="Run mutation self-tests verifying that broken assertions fail")
  args = parser.parse_args()

  checker = PolicyChecker()
  sources_count, files_scanned, assertions_count = checker.check_storage_policy(ROOT)
  print(
    f"Evaluated storage policy facts: sources={sources_count}, files_scanned={files_scanned}, assertions={assertions_count}"
  )

  for w in checker.warnings:
    print(w)
  for f in checker.failures:
    print(f)

  if checker.failures:
    return 1

  if args.mutation_verify:
    mutation_check(ROOT)
    print("PASS: all mutation tests were rejected as expected")

  print("PASS: storage protection policy matches statically visible manifests and scripts")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
