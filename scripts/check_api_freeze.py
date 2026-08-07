#!/usr/bin/env python3
"""Fail when Repo A breaks the frozen HTTP contract.

The baseline is a canonical OpenAPI document captured at the declared whole-app
boundary. This checker intentionally permits additive routes, responses, optional
fields, and enum values, while rejecting removals and newly required inputs.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "contracts" / "repo_a_openapi_a1d3846.json"
BASELINE_SHA256 = "66c71ddb741bb51b5e650f333810c871ccb8dc56623f996f529b7daf0d0e618f"
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _candidate_schema() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "backend"))
    from main import app  # noqa: PLC0415

    return app.openapi()


def _ref(schema: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, dict) or "$ref" not in value:
        return value
    node: Any = schema
    for part in value["$ref"].removeprefix("#/").split("/"):
        if part:
            node = node[part]
    return node


def _compare_schema(
    baseline_root: dict[str, Any],
    candidate_root: dict[str, Any],
    baseline: Any,
    candidate: Any,
    location: str,
    errors: list[str],
    seen: set[tuple[str, str]],
) -> None:
    baseline = _ref(baseline_root, baseline)
    candidate = _ref(candidate_root, candidate)
    marker = (location, json.dumps(baseline, sort_keys=True, default=str))
    if marker in seen:
        return
    seen.add(marker)
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        if baseline != candidate:
            errors.append(f"{location}: changed from {baseline!r} to {candidate!r}")
        return

    for key in ("type", "format"):
        if key in baseline and candidate.get(key) != baseline[key]:
            errors.append(
                f"{location}: {key} changed from {baseline[key]!r} to {candidate.get(key)!r}"
            )

    if "enum" in baseline:
        removed = set(baseline["enum"]) - set(candidate.get("enum", []))
        if removed:
            errors.append(f"{location}: removed enum values {sorted(removed)!r}")

    baseline_required = set(baseline.get("required", []))
    candidate_required = set(candidate.get("required", []))
    added_required = candidate_required - baseline_required
    if added_required:
        errors.append(f"{location}: newly required fields {sorted(added_required)!r}")

    baseline_properties = baseline.get("properties", {})
    candidate_properties = candidate.get("properties", {})
    for name, old_property in baseline_properties.items():
        if name not in candidate_properties:
            errors.append(f"{location}: removed property {name!r}")
            continue
        _compare_schema(
            baseline_root,
            candidate_root,
            old_property,
            candidate_properties[name],
            f"{location}.{name}",
            errors,
            seen,
        )

    for key in ("items", "additionalProperties"):
        if isinstance(baseline.get(key), dict):
            if not isinstance(candidate.get(key), dict):
                errors.append(f"{location}: removed {key} schema")
            else:
                _compare_schema(
                    baseline_root,
                    candidate_root,
                    baseline[key],
                    candidate[key],
                    f"{location}.{key}",
                    errors,
                    seen,
                )

    for keyword in ("anyOf", "oneOf", "allOf"):
        if keyword in baseline:
            old_options = baseline[keyword]
            new_options = candidate.get(keyword, [])
            if len(new_options) < len(old_options):
                errors.append(f"{location}: narrowed {keyword} alternatives")


def main() -> int:
    baseline_bytes = BASELINE.read_bytes()
    actual_hash = hashlib.sha256(baseline_bytes).hexdigest()
    if actual_hash != BASELINE_SHA256:
        print(
            "Frozen OpenAPI artifact changed. A new hash requires an explicitly reviewed "
            "boundary declaration.",
            file=sys.stderr,
        )
        return 1
    baseline = json.loads(baseline_bytes)
    candidate = _candidate_schema()
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()

    for path, old_path in baseline.get("paths", {}).items():
        new_path = candidate.get("paths", {}).get(path)
        if new_path is None:
            errors.append(f"removed path {path}")
            continue
        for method, old_operation in old_path.items():
            if method not in HTTP_METHODS:
                continue
            new_operation = new_path.get(method)
            where = f"{method.upper()} {path}"
            if new_operation is None:
                errors.append(f"removed operation {where}")
                continue

            old_security = old_operation.get("security", baseline.get("security", []))
            new_security = new_operation.get("security", candidate.get("security", []))
            if not old_security and new_security:
                errors.append(f"{where}: authentication became required")

            old_parameters = {
                (p.get("in"), p.get("name")): p for p in old_operation.get("parameters", [])
            }
            new_parameters = {
                (p.get("in"), p.get("name")): p for p in new_operation.get("parameters", [])
            }
            for identity, old_parameter in old_parameters.items():
                if identity not in new_parameters:
                    errors.append(f"{where}: removed parameter {identity}")
                    continue
                new_parameter = new_parameters[identity]
                if old_parameter.get("required") is not True and new_parameter.get("required") is True:
                    errors.append(f"{where}: parameter {identity} became required")
                _compare_schema(
                    baseline,
                    candidate,
                    old_parameter.get("schema", {}),
                    new_parameter.get("schema", {}),
                    f"{where} parameter {identity}",
                    errors,
                    seen,
                )
            for identity, new_parameter in new_parameters.items():
                if identity not in old_parameters and new_parameter.get("required") is True:
                    errors.append(f"{where}: added required parameter {identity}")

            old_body = old_operation.get("requestBody")
            new_body = new_operation.get("requestBody")
            if old_body and not new_body:
                errors.append(f"{where}: removed request body")
            elif old_body and new_body:
                if old_body.get("required") is not True and new_body.get("required") is True:
                    errors.append(f"{where}: request body became required")
                for media_type, old_media in old_body.get("content", {}).items():
                    new_media = new_body.get("content", {}).get(media_type)
                    if new_media is None:
                        errors.append(f"{where}: removed request media type {media_type}")
                        continue
                    _compare_schema(
                        baseline,
                        candidate,
                        old_media.get("schema", {}),
                        new_media.get("schema", {}),
                        f"{where} request {media_type}",
                        errors,
                        seen,
                    )

            for status, old_response in old_operation.get("responses", {}).items():
                new_response = new_operation.get("responses", {}).get(status)
                if new_response is None:
                    errors.append(f"{where}: removed response status {status}")
                    continue
                for media_type, old_media in old_response.get("content", {}).items():
                    new_media = new_response.get("content", {}).get(media_type)
                    if new_media is None:
                        errors.append(f"{where}: removed response media type {status} {media_type}")
                        continue
                    _compare_schema(
                        baseline,
                        candidate,
                        old_media.get("schema", {}),
                        new_media.get("schema", {}),
                        f"{where} response {status} {media_type}",
                        errors,
                        seen,
                    )

    if errors:
        print("Repo A API freeze violation(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Repo A API compatibility: PASS ({len(baseline['paths'])} frozen paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
