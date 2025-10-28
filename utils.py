
from typing import Iterable, Set

def csv_to_set(s: str | None) -> Set[str]:
    if not s:
        return set()
    return {x.strip() for x in s.split(",") if x.strip()}
