from abc import ABC, abstractmethod

import pandas as pd


class SourceAdapter(ABC):
    """Contract for reading the current reporting source."""

    @abstractmethod
    def read(self) -> pd.DataFrame:
        """Return the complete configured flight/rolling window."""
