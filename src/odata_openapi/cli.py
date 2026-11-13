"""odata-openapi command line: download | generate | serve | build."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, config
from .csdl import parse_metadata
from .download import download_metadata, fetch_attributes, load_attributes
from .http import AUTH_CHOICES, ODataError, make_session
from .openapi import ALL_OPERATIONS, SECURITY_SCHEMES, SpecBuilder, select_entity_sets

import truststore
truststore.inject_into_ssl()


# --- shared option groups ------------------------------------------------------
def _connection(p: argparse.ArgumentParser, base_url_required: bool = False):
    g = p.add_argument_group("connection")
    g.add_argument("--base-url", required=base_url_required,
                   help=f"Web API root, e.g. https://host/org/api/data/v9.1 (or ${config.ENV_BASE_URL})")
    g.add_argument("--auth", choices=AUTH_CHOICES, default="sspi",
                   help=f"sspi = Windows single sign-on (default), "
                        f"ntlm = explicit user/password (from ${config.ENV_USER}/${config.ENV_PASSWORD} or prompt), "
                        f"none = no auth (testing only)")
    g.add_argument("--user", help=f"User for ntlm, e.g. DOMAIN\\username (or ${config.ENV_USER})")
    g.add_argument("--ca-bundle", help="CA certificate bundle for an internal CA")
    g.add_argument("--insecure", action="store_true", help="Skip TLS verification (testing only)")


def _selection(p: argparse.ArgumentParser):
    g = p.add_argument_group("selection")
    g.add_argument("--include", action="append", metavar="PATTERN",
                   help="Entity sets to include (glob, repeatable). Default: * (all).")
    g.add_argument("--exclude", action="append", metavar="PATTERN", default=[],
                   help="Entity sets to exclude (glob, repeatable).")


def _generation(p: argparse.ArgumentParser):
    g = p.add_argument_group("generation")
    g.add_argument("--operations", default=",".join(ALL_OPERATIONS),
                   help=f"Comma-separated subset of {','.join(ALL_OPERATIONS)} (default: all)")
    g.add_argument("--security", choices=[*SECURITY_SCHEMES, "none"], default="negotiate",
                   help="Security scheme declared in the spec (default: negotiate)")
    g.add_argument("--title", default="Dataverse Web API", help="Spec title")


def _session(args):
    verify = False if args.insecure else (args.ca_bundle or True)
    if args.insecure:
        import urllib3
        urllib3.disable_warnings()
    return make_session(args.auth, user=args.user, verify=verify)


def _includes(args):
    return args.include or list(config.DEFAULT_INCLUDE)


def _operations(args):
    ops = [o.strip() for o in args.operations.split(",") if o.strip()]
    bad = set(ops) - set(ALL_OPERATIONS)
    if bad:
        raise ODataError(f"Unknown operations: {', '.join(sorted(bad))}")
    return ops


def _enrich(session, base_url, model, sets, out_path):
    names = {model.entity_sets[s] for s in sets if model.is_a(model.entity_sets[s], "crmbaseentity")}
    print(f"Fetching column metadata for {len(names)} tables ...", file=sys.stderr)
    return fetch_attributes(session, base_url, names, out_path)


def _generate(model, args, base_url, attributes):
    sets = select_entity_sets(model, _includes(args), args.exclude)
    if not sets:
        raise ODataError(f"No entity sets match --include {_includes(args)} --exclude {args.exclude}")
    spec = SpecBuilder(model, base_url, attributes, _operations(args), args.security, args.title).build(sets)
    out = Path(args.output)
    out.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    ops = sum(1 for p in spec["paths"].values() for m in p)
    print(f"Wrote {out}: {len(sets)} entity sets, {len(spec['paths'])} paths, {ops} operations, "
          f"{out.stat().st_size / 1e6:.1f} MB", file=sys.stderr)
    return spec


# --- commands --------------------------------------------------------------------
def cmd_download(args):
    base_url = config.resolve_base_url(args.base_url)
    session = _session(args)
    out = download_metadata(session, base_url, args.output)
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB)", file=sys.stderr)
    if args.enrich:
        model = parse_metadata(out)
        sets = select_entity_sets(model, _includes(args), args.exclude)
        _enrich(session, base_url, model, sets, args.attributes_out)
        print(f"Wrote {args.attributes_out}", file=sys.stderr)


def cmd_generate(args):
    model = parse_metadata(Path(args.metadata))
    _generate(model, args, config.resolve_base_url(args.base_url), load_attributes(args.attributes))


def cmd_serve(args):
    from .serve import serve
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    upstream = session = None
    if not args.no_proxy:
        upstream = args.base_url or (spec.get("servers") or [{}])[0].get("url")
        session = _session(args)
    serve(spec, args.host, args.port, upstream, session, open_browser=not args.no_browser)


def cmd_build(args):
    base_url = config.resolve_base_url(args.base_url)
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    session = _session(args)
    meta = download_metadata(session, base_url, workdir / "metadata.xml")
    print(f"Wrote {meta}", file=sys.stderr)
    model = parse_metadata(meta)
    attributes = {}
    if args.enrich:
        sets = select_entity_sets(model, _includes(args), args.exclude)
        attributes = _enrich(session, base_url, model, sets, workdir / "attributes.json")
    spec = _generate(model, args, base_url, attributes)
    if args.serve:
        from .serve import serve
        serve(spec, args.host, args.port, base_url, session, open_browser=not args.no_browser)


def _serve_options(p):
    g = p.add_argument_group("server")
    g.add_argument("--host", default="127.0.0.1")
    g.add_argument("--port", type=int, default=8000)
    g.add_argument("--no-browser", action="store_true", help="Don't open a browser")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="odata-openapi", description=__doc__)
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("download", help="Download $metadata (and optionally column metadata)")
    p.add_argument("-o", "--output", default="metadata.xml")
    p.add_argument("--enrich", action="store_true",
                   help="Also fetch required/choice/label info for the selected tables (recommended)")
    p.add_argument("--attributes-out", default="attributes.json")
    _connection(p, base_url_required=True); _selection(p)
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("generate", help="Generate an OpenAPI 3.0 spec from a downloaded $metadata (offline)")
    p.add_argument("metadata", help="Path to metadata.xml")
    p.add_argument("-o", "--output", default="openapi.json")
    p.add_argument("--attributes", help="attributes.json from 'download --enrich'")
    p.add_argument("--base-url", help="Server URL written into the spec")
    _selection(p); _generation(p)
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("serve", help="Serve Swagger UI for a spec, with an authenticated proxy for 'Try it out'")
    p.add_argument("spec", help="Path to openapi.json")
    p.add_argument("--no-proxy", action="store_true", help="Only show the docs; don't forward requests")
    _connection(p); _serve_options(p)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("build", help="download + (enrich) + generate [+ serve] in one go")
    p.add_argument("-o", "--output", default="openapi.json")
    p.add_argument("--workdir", default=".", help="Where metadata.xml / attributes.json are saved")
    p.add_argument("--enrich", action="store_true", help="Fetch column metadata (recommended)")
    p.add_argument("--serve", action="store_true", help="Start Swagger UI afterwards")
    _connection(p, base_url_required=True); _selection(p); _generation(p); _serve_options(p)
    p.set_defaults(func=cmd_build)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except ODataError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
