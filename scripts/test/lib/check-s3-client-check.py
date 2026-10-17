#!/usr/bin/env python3
"""R272: scripts/verify/s3-client-check.py against stubbed boto3/botocore (run from the repo root).

Proves: anonymous requests use botocore.UNSIGNED (the string "unsigned" is not a valid signature
version), a deny is only a ClientError 403/AccessDenied, success or a non-ClientError is a FAIL, and
a crash in one step is a FAIL line, not a traceback."""
import importlib.util
import io
import sys
import types

UNSIGNED = object()


class ClientError(Exception):
    def __init__(self, code, status=403):
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class Config:
    def __init__(self, signature_version=None, **kw):
        self.signature_version = signature_version


def install(behaviour):
    boto3 = types.ModuleType("boto3")
    botocore = types.ModuleType("botocore")
    botocore.UNSIGNED = UNSIGNED
    cfg = types.ModuleType("botocore.config"); cfg.Config = Config
    exc = types.ModuleType("botocore.exceptions"); exc.ClientError = ClientError

    class Client:
        def __init__(self, config, signed):
            if config is not None and config.signature_version not in (None, UNSIGNED):
                raise ValueError("UnknownSignatureVersionError")  # what the string "unsigned" caused
            self.signed = signed

        def put_object(self, Bucket, Key, Body):
            if Bucket == "other":
                return behaviour["cross"](self)
            return {}

        def upload_fileobj(self, *a): return None
        def get_object(self, **kw): return {"Body": io.BytesIO(b"x")}
        def delete_objects(self, **kw): return {}
        def list_objects_v2(self, **kw): return behaviour["anon"](self)

    def make(service, **kw):
        c = kw.get("config")
        return Client(c, signed="aws_access_key_id" in kw)

    boto3.client = make
    sys.modules.update({"boto3": boto3, "botocore": botocore, "botocore.config": cfg, "botocore.exceptions": exc})


def deny(_self): raise ClientError("AccessDenied")
def ok(_self): return {}
def boom(_self): raise RuntimeError("endpoint down")


spec = importlib.util.spec_from_file_location("s3cc", "scripts/verify/s3-client-check.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


def run(behaviour):
    install(behaviour)
    out = io.StringIO(); old = sys.stdout; sys.stdout = out
    try:
        rc = mod.main(io.StringIO("AK\nSK\nown\nother\n"))
    finally:
        sys.stdout = old
    return rc, out.getvalue()


rc, out = run({"cross": deny, "anon": deny})
assert rc == 0 and out.count("PASS") == 6 and "FAIL" not in out, out
rc, out = run({"cross": ok, "anon": deny})
assert rc == 1 and "FAIL cross-bucket deny" in out, out
rc, out = run({"cross": deny, "anon": ok})
assert rc == 1 and "FAIL anonymous deny" in out, out
rc, out = run({"cross": deny, "anon": boom})
assert rc == 1 and "FAIL anonymous deny" in out and "Traceback" not in out, out  # a crash is not a deny
rc, out = run({"cross": lambda s: (_ for _ in ()).throw(ClientError("NoSuchBucket", 404)), "anon": deny})
assert rc == 1 and "FAIL cross-bucket deny" in out, out  # wrong error is not a deny
src = open("scripts/verify/s3-client-check.py").read()
assert "from botocore import UNSIGNED" in src and "signature_version=UNSIGNED" in src and '"unsigned"' not in src
print("OK")
