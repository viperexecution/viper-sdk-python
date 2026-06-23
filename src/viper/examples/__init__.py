"""
Viper SDK — runnable examples.

Each example is a module in this package that exposes:

    ORDER        : int    display/order key (lower = listed first)
    DESCRIPTION  : str    one-line summary shown in the catalog
    async main() : the example body

They ship inside the installed package, so after `pip install viper-execution`
you can list and run them with no extra downloads:

    viper-examples                 # list the catalog
    viper-examples <name>          # run one by slug or order number
    python -m viper.examples       # equivalent

Add an example by dropping a new module in this package with those three
attributes — it appears in the catalog automatically.
"""
from __future__ import annotations

import asyncio
import importlib
import pkgutil
import sys
from typing import Dict, List, Tuple


def _discover() -> List[Tuple[str, str, int, str]]:
    """Return [(slug, module_name, order, description), ...] sorted by order."""
    found = []
    for info in pkgutil.iter_modules(__path__):
        name = info.name
        if name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{name}")
        if not hasattr(mod, "main") or not hasattr(mod, "DESCRIPTION"):
            continue
        order = int(getattr(mod, "ORDER", 999))
        slug = name.replace("_", "-")
        found.append((slug, name, order, str(mod.DESCRIPTION)))
    found.sort(key=lambda r: (r[2], r[0]))
    return found


def list_examples() -> List[Tuple[str, str, int, str]]:
    """Public helper: the discovered example catalog."""
    return _discover()


def _resolve(token: str, catalog) -> str | None:
    """Map a user token (slug, module name, or order number) to a module name."""
    t = token.strip().lower().replace("_", "-")
    for slug, mod_name, order, _desc in catalog:
        if t == slug or t == mod_name.replace("_", "-"):
            return mod_name
        if t.lstrip("0") == str(order) or t == f"{order:02d}":
            return mod_name
    return None


def _print_catalog(catalog) -> None:
    print("Viper SDK — Examples")
    print("====================")
    print("Runnable examples shipped with the SDK. Run one by name or number:\n")
    print("    viper-examples <name>\n")
    if not catalog:
        print("  (no examples found)")
    else:
        print("Available examples:")
        width = max(len(s) for s, *_ in catalog)
        for slug, _mod, order, desc in catalog:
            print(f"  {order:02d}  {slug.ljust(width)}   {desc}")
    print()
    print("Required env vars (live examples):")
    print("    VIPER_API_KEY       (vk_...)            required")
    print("    VIPER_API_SECRET    (vs_...)            required")
    print("    VIPER_HANDLE        your handle         optional")
    print("    VIPER_WALLET        0x... to stream     example-dependent")
    print()
    print("Examples:")
    print("    viper-examples list")
    if catalog:
        print(f"    viper-examples {catalog[0][0]}")
        print(f"    viper-examples {catalog[0][2]:02d}")


def cli(argv=None) -> int:
    """Console entry point: `viper-examples [name]`."""
    argv = list(sys.argv[1:] if argv is None else argv)
    catalog = _discover()

    if not argv or argv[0] in ("list", "ls", "-h", "--help", "help"):
        _print_catalog(catalog)
        return 0

    token = argv[0]
    mod_name = _resolve(token, catalog)
    if mod_name is None:
        print(f"Unknown example: {token!r}\n")
        _print_catalog(catalog)
        return 2

    mod = importlib.import_module(f"{__name__}.{mod_name}")
    try:
        asyncio.run(mod.main())
    except KeyboardInterrupt:
        print("\n# interrupted — stopping")
        return 130
    return 0
