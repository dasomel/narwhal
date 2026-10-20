#!/usr/bin/env python3
"""Per-client SeaweedFS S3 check (docs/runbooks/seaweedfs-s3-auth.md, D-s3auth).

stdin, one per line: access key, secret key, own bucket, another client's bucket (keys never go on
argv). Optional env S3_ENDPOINT. Needs boto3 (present in the CNPG postgres image). Prints PASS/FAIL
per step and exits 1 on any FAIL:
put, multipart (11 MiB), get, delete, write to the other bucket denied, anonymous list denied."""
import io
import os
import sys

ENDPOINT = os.environ.get("S3_ENDPOINT", "http://seaweedfs-s3.storage.svc.cluster.local:8333")
DENY = ("AccessDenied", "403", "SignatureDoesNotMatch", "InvalidAccessKeyId")


def main(stdin=sys.stdin):
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config
    from botocore.exceptions import ClientError

    ak, sk, bucket, other = [stdin.readline().strip() for _ in range(4)]
    if not all((ak, sk, bucket, other)):
        print("FAIL usage: need access key, secret key, bucket, other bucket on stdin")
        return 1

    def client(**kw):
        return boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1", **kw)

    c = client(aws_access_key_id=ak, aws_secret_access_key=sk, config=Config(s3={"addressing_style": "path"}))
    anon = client(config=Config(signature_version=UNSIGNED, s3={"addressing_style": "path"}))
    failed = []

    def step(name, fn):
        try:
            ok = fn()
        except Exception as e:  # any error is a FAIL, never an unhandled traceback or a false PASS
            ok = False
            name = f"{name} ({type(e).__name__})"
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            failed.append(name)

    def denied(fn):
        # a deny is exactly a ClientError with a deny code; success or any other error is not a deny
        try:
            fn()
        except ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            return code in DENY or str(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")) == "403"
        return False

    step("put", lambda: c.put_object(Bucket=bucket, Key="s3auth/small", Body=b"x") is not None)
    step("multipart", lambda: c.upload_fileobj(io.BytesIO(b"y" * (11 * 1024 * 1024)), bucket, "s3auth/big") is None)
    step("get", lambda: c.get_object(Bucket=bucket, Key="s3auth/small")["Body"].read() == b"x")
    step("delete", lambda: c.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": "s3auth/small"}, {"Key": "s3auth/big"}]}) is not None)
    step("cross-bucket deny", lambda: denied(lambda: c.put_object(Bucket=other, Key="s3auth/x", Body=b"x")))
    step("anonymous deny", lambda: denied(lambda: anon.list_objects_v2(Bucket=bucket)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
