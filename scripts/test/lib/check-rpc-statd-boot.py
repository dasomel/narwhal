#!/usr/bin/env python3
import pathlib
import sys


PREREQUISITES = pathlib.Path("scripts/common/01-prerequisites.sh")


def check(source):
  required = [
    "[Unit]",
    "After=rpcbind.service network-online.target",
    "Wants=rpcbind.service network-online.target",
    "[Install]",
    "WantedBy=multi-user.target",
    "systemctl daemon-reload",
    "systemctl add-wants multi-user.target rpc-statd.service",
    "systemctl start rpc-statd.service",
  ]
  missing = [line for line in required if line not in source]
  if missing:
    raise AssertionError(f"missing rpc-statd boot setup: {missing}")

  vagrant = pathlib.Path("Vagrantfile").read_text()
  if vagrant.count('path: "scripts/common/01-prerequisites.sh"') < 2:
    raise AssertionError("Vagrant master and worker provisioning must share prerequisites")

  kakao = pathlib.Path("scripts/cloud/provision-kakao.sh").read_text()
  if "common/01-prerequisites.sh" not in kakao:
    raise AssertionError("Kakao provider does not stage shared node prerequisites")


def main():
  source = PREREQUISITES.read_text()
  check(source)
  if "--mutation-verify" in sys.argv:
    mutation = source.replace(
      "sudo systemctl add-wants multi-user.target rpc-statd.service\n", "", 1
    )
    try:
      check(mutation)
    except AssertionError:
      print("PASS: mutation removing boot dependency was rejected")
    else:
      raise AssertionError("mutation removing boot dependency was accepted")
  print("PASS: shared node prerequisites enable rpc-statd on Vagrant and Kakao nodes")


if __name__ == "__main__":
  main()
