#!/usr/bin/env python3
"""Dump every host in AAP 2.6 to CSV, with the inventory and organization
each host belongs to.

Pulls /hosts/ (every host across all inventories the caller can see) plus
/organizations/ (to resolve org names). Run it as a system administrator or
system auditor to get everything. Uses only the Python standard library (3.8+).

Usage:
    export AAP_HOST=https://aap.example.com
    export AAP_TOKEN=xxxxxxxx                 # or use --username (prompts for password)
    python3 export_hosts.py -o hosts.csv

Token: create it in the platform gateway (User > API tokens, or POST /api/gateway/v1/tokens/).
Note: hosts in constructed inventories are copies of hosts from their source
inventories, so they show up twice. Filter on inventory_kind if you don't want
them. Smart inventories don't own hosts, so they never appear in this dump.
"""
import argparse
import base64
import csv
import getpass
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

API_PREFIX = "/api/controller/v2"

COLUMNS = [
    "host_id", "host_name", "enabled",
    "inventory_id", "inventory_name", "inventory_kind",
    "organization_id", "organization",
    "description", "instance_id", "has_active_failures",
    "last_job_id", "last_job_status", "last_job_finished",
    "created", "modified",
]


def parse_args():
    env = os.environ.get
    p = argparse.ArgumentParser(description="Dump AAP 2.6 hosts with their inventory to CSV")
    p.add_argument("--host", default=env("AAP_HOST"), help="AAP URL (env AAP_HOST)")
    p.add_argument("--token", default=env("AAP_TOKEN"), help="OAuth token (env AAP_TOKEN)")
    p.add_argument("--username", default=env("AAP_USERNAME"), help="basic-auth user (env AAP_USERNAME)")
    p.add_argument("--password", default=env("AAP_PASSWORD"), help="basic-auth password (env AAP_PASSWORD)")
    p.add_argument("--api-prefix", default=API_PREFIX, help=f"API prefix (default {API_PREFIX})")
    p.add_argument("--insecure", action="store_true",
                   default=str(env("AAP_VALIDATE_CERTS", "true")).lower() in ("0", "false", "no"),
                   help="skip TLS certificate verification")
    p.add_argument("--page-size", type=int, default=200, help="max 200 unless MAX_PAGE_SIZE was raised")
    p.add_argument("-o", "--output", default="hosts.csv", help="CSV path, or - for stdout")
    a = p.parse_args()

    if not a.host:
        p.error("--host or AAP_HOST is required")
    if not a.token and not a.username:
        p.error("provide --token/AAP_TOKEN or --username/AAP_USERNAME")
    if a.username and not a.token and not a.password:
        a.password = getpass.getpass(f"Password for {a.username}: ")
    a.host = a.host.rstrip("/")
    return a


def get_all(first_url, host, headers, ctx):
    """Follow 'next' links and return every result."""
    url, out = first_url, []
    while url:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
            data = json.load(resp)
        out.extend(data.get("results", []))
        nxt = data.get("next")
        url = urllib.parse.urljoin(host + "/", nxt) if nxt else None
        print(f"  {len(out)}/{data.get('count', '?')}", file=sys.stderr, end="\r")
    print(file=sys.stderr)
    return out


def to_row(h, orgs):
    sf = h.get("summary_fields") or {}
    inv = sf.get("inventory") or {}
    job = sf.get("last_job") or {}
    org_id = inv.get("organization_id", "")
    return {
        "host_id": h.get("id", ""),
        "host_name": h.get("name", ""),
        "enabled": h.get("enabled", ""),
        "inventory_id": h.get("inventory", inv.get("id", "")),
        "inventory_name": inv.get("name", ""),
        "inventory_kind": inv.get("kind") or "standard",
        "organization_id": org_id,
        "organization": orgs.get(org_id, ""),
        "description": h.get("description", ""),
        "instance_id": h.get("instance_id", ""),
        "has_active_failures": h.get("has_active_failures", ""),
        "last_job_id": job.get("id", ""),
        "last_job_status": job.get("status", ""),
        "last_job_finished": job.get("finished") or "",
        "created": h.get("created", ""),
        "modified": h.get("modified", ""),
    }


def main():
    a = parse_args()

    headers = {"Accept": "application/json"}
    if a.token:
        headers["Authorization"] = f"Bearer {a.token}"
    else:
        cred = base64.b64encode(f"{a.username}:{a.password}".encode()).decode()
        headers["Authorization"] = f"Basic {cred}"

    ctx = ssl.create_default_context()
    if a.insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    base = f"{a.host}{a.api_prefix}"
    try:
        print("Fetching organizations...", file=sys.stderr)
        orgs = {o["id"]: o["name"] for o in get_all(
            f"{base}/organizations/?page_size={a.page_size}", a.host, headers, ctx)}
        print("Fetching hosts...", file=sys.stderr)
        hosts = get_all(f"{base}/hosts/?page_size={a.page_size}&order_by=inventory__name,name",
                        a.host, headers, ctx)
    except urllib.error.HTTPError as e:
        hint = " (check token/credentials)" if e.code in (401, 403) else ""
        hint = " (wrong API prefix for this AAP version?)" if e.code == 404 else hint
        sys.exit(f"HTTP {e.code} from {e.url}{hint}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach {a.host}: {e.reason}")

    rows = [to_row(h, orgs) for h in hosts]

    out = sys.stdout if a.output == "-" else open(a.output, "w", newline="", encoding="utf-8")
    try:
        w = csv.DictWriter(out, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    finally:
        if out is not sys.stdout:
            out.close()

    n_inv = len({r["inventory_id"] for r in rows})
    print(f"Wrote {len(rows)} hosts across {n_inv} inventories to {a.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
