---
name: python-expert
description: Expert Python coding assistant with best practices for modern Python development
triggers: python, py, pytest, fastapi, django, flask, asyncio, pydantic
---

# Python Expert Skill

When working on Python code:

## Style & conventions
- Use Python 3.10+ features: match/case, `|` union types, structural pattern matching
- Prefer `dataclasses` or `pydantic` over plain dicts for data models
- Use `pathlib.Path` over `os.path`
- Use `f-strings` over `.format()` or `%` formatting
- Type-annotate all function signatures

## Testing
- Write `pytest` tests, not `unittest`
- Use `pytest.fixture` for setup
- Parametrize with `@pytest.mark.parametrize`
- Mock with `pytest-mock`'s `mocker` fixture

## Error handling
- Raise specific exceptions, not bare `Exception`
- Use `contextlib.suppress` for expected exceptions in cleanup paths
- Document exceptions in docstrings

## Performance
- Prefer generators over list comprehensions when only iterating
- Use `functools.lru_cache` / `functools.cache` for pure functions
- Profile before optimizing — use `cProfile` or `py-spy`
