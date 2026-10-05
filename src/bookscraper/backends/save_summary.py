"""The ``SaveSummary`` result type shared by both storage backends.

It lives in its own leaf module (no imports from the rest of ``bookscraper.backends``) so
``backends/storage.py``, ``backends/local/store.py`` and ``backends/mongo/database.py`` can all
import it at module level without an import cycle. ``backends.storage`` re-exports it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SaveSummary:
    """
    Outcome of a :meth:`StorageBackend.save_books` call, identical in shape for
    both backends so a caller can distinguish a 100%-duplicate batch from a total
    failure (which it could not when ``save_books`` returned ``None``).

    :ivar inserted: Documents newly written.
    :ivar duplicates: Documents skipped because their ``hash`` already exists.
    :ivar errors: Documents rejected for any other reason.
    """

    inserted: int
    duplicates: int
    errors: int

    @property
    def total(self) -> int:
        """Total documents accounted for: ``inserted + duplicates + errors``."""
        return self.inserted + self.duplicates + self.errors
