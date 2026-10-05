from salyq.tax.config import ConfigNotFound, LoadedConfig, available_years, load_year
from salyq.tax.engine import (
    SimplifiedResult,
    SocialResult,
    TaxInputError,
    TaxWarning,
    calculate_self_social,
    calculate_simplified,
)

__all__ = [
    "ConfigNotFound", "LoadedConfig", "available_years", "load_year", "SimplifiedResult",
    "SocialResult", "TaxInputError", "TaxWarning", "calculate_self_social", "calculate_simplified",
]
