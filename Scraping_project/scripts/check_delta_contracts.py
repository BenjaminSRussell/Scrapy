#!/usr/bin/env python3
"""Check docs/contracts JSON Schemas against Delta writers and SCHEMA_REGISTRY.

Stdlib only. Does not import the pipeline, read a lake, or crawl. Exit 1 when
a registered contract and the code that writes it disagree (#1099).
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONTRACTS = REPO / "docs" / "contracts"
SCHEMAS_PY = REPO / "Scraping_project" / "src" / "core" / "schemas.py"
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
JSON_TYPES = {"string", "integer", "number", "boolean", "array", "object"}


def load_registry() -> dict:
    path = CONTRACTS / "registry.json"
    return json.loads(path.read_text())


def check() -> list[str]:
    errors: list[str] = []
    if not CONTRACTS.is_dir():
        return [f"missing contract directory: {CONTRACTS}"]
    try:
        registry = load_registry()
    except (OSError, json.JSONDecodeError) as exc:
        return [f"registry.json: {exc}"]

    entries = registry.get("contracts")
    if not isinstance(entries, list) or len(entries) < 3:
        errors.append("registry.json must list at least 3 contracts")
        entries = entries if isinstance(entries, list) else []
    if not SEMVER.match(str(registry.get("registry_version", ""))):
        errors.append("registry.json registry_version must be MAJOR.MINOR.PATCH")

    on_disk = sorted(p.name for p in CONTRACTS.glob("*.schema.json"))
    listed = [e.get("schema") for e in entries if isinstance(e, dict)]
    if sorted(s for s in listed if isinstance(s, str)) != on_disk:
        errors.append(
            f"registry.json schemas {listed} do not match files {on_disk}"
        )

    try:
        arrow = _arrow_registry(SCHEMAS_PY.read_text())
    except (OSError, ValueError) as exc:
        errors.append(f"schemas.py: {exc}")
        arrow = {}

    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append(f"registry entry is not an object: {entry!r}")
            continue
        name = entry.get("name")
        schema_name = entry.get("schema")
        table = entry.get("delta_table")
        if not isinstance(name, str) or name in seen:
            errors.append(f"duplicate or missing contract name: {name!r}")
            continue
        seen.add(name)
        if not isinstance(schema_name, str):
            errors.append(f"{name}: registry schema filename missing")
            continue
        path = CONTRACTS / schema_name
        try:
            schema = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{name}: {exc}")
            continue
        if schema.get("title") != name:
            errors.append(f"{name}: title is {schema.get('title')!r}")
        if schema.get("x-delta-table") != table:
            errors.append(
                f"{name}: x-delta-table {schema.get('x-delta-table')!r} != registry {table!r}"
            )
        errors.extend(_check_schema(name, schema, arrow.get(table) if isinstance(table, str) else None))
    return errors


def _check_schema(name: str, schema: dict, arrow: dict[str, str] | None) -> list[str]:
    errors: list[str] = []
    if schema.get("type") != "object":
        errors.append(f"{name}: schema type must be object")
    if not SEMVER.match(str(schema.get("x-contract-version", ""))):
        errors.append(f"{name}: x-contract-version must be MAJOR.MINOR.PATCH")
    if schema.get("additionalProperties") is not True:
        errors.append(f"{name}: additionalProperties must stay true")

    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not properties:
        return errors + [f"{name}: properties missing"]
    if not isinstance(required, list) or not all(isinstance(f, str) for f in required):
        return errors + [f"{name}: required must be a list of strings"]

    derived: set[str] = set()
    for prop, spec in properties.items():
        if not isinstance(spec, dict):
            errors.append(f"{name}.{prop}: property spec must be an object")
            continue
        json_type = spec.get("type")
        if json_type not in JSON_TYPES:
            errors.append(f"{name}.{prop}: unsupported JSON type {json_type!r}")
        if json_type == "array":
            items = spec.get("items")
            if not isinstance(items, dict) or items.get("type") not in JSON_TYPES:
                errors.append(f"{name}.{prop}: array items need a JSON type")
        if spec.get("x-derived") is True:
            derived.add(prop)
            if "x-arrow" in spec:
                errors.append(f"{name}.{prop}: derived columns are not in SCHEMA_REGISTRY")
            continue
        if arrow is None:
            errors.append(f"{name}: no SCHEMA_REGISTRY entry for {schema.get('x-delta-table')!r}")
            arrow = {}
        if prop not in arrow:
            errors.append(f"{name}.{prop}: missing from SCHEMA_REGISTRY")
        elif spec.get("x-arrow") != arrow[prop]:
            errors.append(
                f"{name}.{prop}: x-arrow {spec.get('x-arrow')!r} != SCHEMA_REGISTRY {arrow.get(prop)!r}"
            )

    partitions = schema.get("x-partition-columns")
    if not isinstance(partitions, list) or not all(isinstance(p, str) for p in partitions):
        errors.append(f"{name}: x-partition-columns must be a list of strings")
        partitions = []
    if set(partitions) != derived:
        errors.append(
            f"{name}: x-partition-columns {partitions} != derived properties {sorted(derived)}"
        )
    for field in required:
        if field not in properties:
            errors.append(f"{name}: required field {field} has no property")
        elif field in derived:
            errors.append(f"{name}: derived column {field} must not be required")

    writers = schema.get("x-writers")
    if not isinstance(writers, list) or not writers:
        return errors + [f"{name}: x-writers missing"]
    covered: set[str] = set()
    stable = [f for f in required if f not in derived]
    for writer in writers:
        if not isinstance(writer, dict):
            errors.append(f"{name}: writer entry must be an object")
            continue
        covers = writer.get("covers", stable)
        if not isinstance(covers, list) or not all(isinstance(c, str) for c in covers):
            errors.append(f"{name}: writer covers must be a list of strings")
            continue
        unknown = [c for c in covers if c not in properties or c in derived]
        if unknown:
            errors.append(f"{name}: writer covers unknown fields {unknown}")
        covered.update(covers)
        rel = writer.get("path")
        qual = writer.get("qualname")
        if not isinstance(rel, str) or not isinstance(qual, str):
            errors.append(f"{name}: writer path and qualname are required")
            continue
        src = REPO / rel
        try:
            tree = ast.parse(src.read_text())
            keys = _function_keys(tree, qual)
        except (OSError, SyntaxError, LookupError) as exc:
            errors.append(f"{name}: {rel} {qual}: {exc}")
            continue
        missing = [c for c in covers if c not in keys]
        if missing:
            errors.append(f"{name}: {qual} does not set {missing}")
    if stable and not set(stable) <= covered:
        errors.append(f"{name}: writers do not cover required fields {sorted(set(stable) - covered)}")
    return errors


def _function_keys(tree: ast.AST, qualname: str) -> set[str]:
    node: ast.AST = tree
    for part in qualname.split("."):
        found = None
        for child in getattr(node, "body", []):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == part:
                found = child
                break
        if found is None:
            raise LookupError(f"{qualname} not found")
        node = found
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise LookupError(f"{qualname} is not a function")
    keys: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            for key in sub.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
        targets: list[ast.AST] = []
        if isinstance(sub, ast.Assign):
            targets.extend(sub.targets)
        elif isinstance(sub, ast.AnnAssign):
            targets.append(sub.target)
        for target in targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                keys.add(target.slice.value)
    return keys


def _arrow_registry(source: str) -> dict[str, dict[str, str]]:
    tree = ast.parse(source)
    declared: dict[str, dict[str, str]] = {}
    registry: dict[str, str] = {}
    for node in tree.body:
        target, value = _assign(node)
        if target is None or value is None:
            continue
        if _is_pa_schema(value):
            declared[target] = _schema_fields(value)
        elif target == "SCHEMA_REGISTRY" and isinstance(value, ast.Dict):
            for key, val in zip(value.keys, value.values, strict=True):
                if isinstance(key, ast.Constant) and isinstance(key.value, str) and isinstance(val, ast.Name):
                    registry[key.value] = val.id
    resolved: dict[str, dict[str, str]] = {}
    for table, schema_name in registry.items():
        if schema_name not in declared:
            raise ValueError(f"SCHEMA_REGISTRY[{table!r}] -> {schema_name} has no pa.schema")
        resolved[table] = declared[schema_name]
    return resolved


def _assign(node: ast.AST) -> tuple[str | None, ast.AST | None]:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id, node.value
    return None, None


def _is_pa_schema(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "schema"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pa"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.List)
    )


def _schema_fields(call: ast.AST) -> dict[str, str]:
    assert isinstance(call, ast.Call)
    arg = call.args[0]
    assert isinstance(arg, ast.List)
    fields: dict[str, str] = {}
    for elt in arg.elts:
        if not isinstance(elt, ast.Tuple) or len(elt.elts) < 2:
            raise ValueError("pa.schema entries must be (name, type) tuples")
        name, typ = elt.elts[0], elt.elts[1]
        if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
            raise ValueError("pa.schema field name must be a string")
        fields[name.value] = _arrow_type(typ)
    return fields


def _arrow_type(node: ast.AST) -> str:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        raise ValueError(f"unsupported Arrow type: {ast.dump(node)}")
    name = node.func.attr
    if name == "list_":
        if not node.args:
            raise ValueError("pa.list_ needs an item type")
        return f"list<{_arrow_type(node.args[0])}>"
    if name == "timestamp":
        if not node.args or not isinstance(node.args[0], ast.Constant):
            raise ValueError("pa.timestamp needs a unit")
        return f"timestamp[{node.args[0].value}]"
    if name == "bool_":
        return "bool"
    if name in {"string", "int32", "int64", "float64"}:
        return name
    raise ValueError(f"unsupported Arrow type pa.{name}")


def main() -> int:
    errors = check()
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    names = [e["name"] for e in load_registry()["contracts"]]
    print(f"ok: {len(names)} delta contracts ({', '.join(names)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
