"""MacBot's import-light public package surface.

Package version and authorship metadata have one authority: ``pyproject.toml``
and the installed distribution metadata generated from it.
"""


def main():
    """Keep the public entry point without importing the CLI during package import."""
    from .cli import main as run

    return run()


__all__ = ["main"]
