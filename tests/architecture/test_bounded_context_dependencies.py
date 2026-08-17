"""Lightweight AST guardrails for bounded-context direction."""

import ast
from pathlib import Path

SOURCE = Path(__file__).parents[2] / "src" / "investment"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
    return imports


def test_bitcoin_research_never_depends_on_crypto_trading() -> None:
    violations = {
        str(path.relative_to(SOURCE)): module
        for path in (SOURCE / "bitcoin").rglob("*.py")
        for module in _imports(path)
        if module.startswith("investment.crypto")
    }
    assert violations == {}


def test_crypto_market_data_is_independent_of_research_and_trading() -> None:
    violations = {
        str(path.relative_to(SOURCE)): module
        for path in (SOURCE / "market_data" / "crypto").rglob("*.py")
        for module in _imports(path)
        if module.startswith(("investment.bitcoin", "investment.crypto"))
    }
    assert violations == {}


def test_crypto_trading_can_only_import_published_bitcoin_contracts() -> None:
    violations = {
        str(path.relative_to(SOURCE)): module
        for path in (SOURCE / "crypto").rglob("*.py")
        for module in _imports(path)
        if module.startswith("investment.bitcoin") and module != "investment.bitcoin.contracts"
    }
    assert violations == {}


def test_domain_modules_do_not_depend_on_web_or_exchange_adapters() -> None:
    forbidden = ("fastapi", "httpx", "investment.interfaces", "investment.crypto.infrastructure")
    violations = {
        str(path.relative_to(SOURCE)): module
        for domain in (SOURCE / "core" / "domain", SOURCE / "crypto" / "domain")
        for path in domain.rglob("*.py")
        for module in _imports(path)
        if module.startswith(forbidden)
    }
    assert violations == {}


def test_legacy_bitcoin_price_modules_only_forward_to_canonical_market_data() -> None:
    for name in ("provider.py", "normalizer.py", "schema.py"):
        path = SOURCE / "bitcoin" / "data" / "price" / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert not any(isinstance(node, (ast.ClassDef, ast.FunctionDef)) for node in tree.body)
