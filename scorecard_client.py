
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
)

SCORECARD_BASE_URL = os.getenv("SCORECARD_BASE_URL", "https://api.data.gov/ed/collegescorecard/v1")
API_KEY = os.getenv("COLLEGE_SCORECARD_API_KEY", "")

AUTOCOMPLETE_FIELDS = "id,school.name,school.city,school.state"

DEFAULT_FIELDS = ",".join([
    "id",
    "school.name","school.city","school.state","school.school_url",
    "location.lat","location.lon",
    "latest.student.size",
    "latest.admissions.admission_rate.overall",
    "latest.cost.tuition.in_state","latest.cost.tuition.out_of_state",
    "latest.cost.attendance.academic_year",
    "latest.completion.rate_suppressed.overall",
])

PROGRAM_FIELDS = ",".join([
    "id",
    "school.name",
    "latest.programs.cip_4_digit.code",
    "latest.programs.cip_4_digit.title",
    "latest.programs.cip_4_digit.credential.level",
    "latest.programs.cip_4_digit.share",
])

DETAIL_FIELDS = ",".join([
    "id",
    "ope6_id","ope8_id",
    "school.name","school.alias","school.city","school.state","school.zip","school.school_url","school.ownership",
    "location.lat","location.lon",
    "latest.student.size","latest.student.demographics.women","latest.student.demographics.men",
    "latest.student.demographics.race_ethnicity.white","latest.student.demographics.race_ethnicity.black",
    "latest.student.demographics.race_ethnicity.hispanic","latest.student.demographics.race_ethnicity.asian",
    "latest.student.demographics.race_ethnicity.aian","latest.student.demographics.race_ethnicity.nhpi",
    "latest.student.demographics.first_generation",
    "latest.admissions.admission_rate.overall","latest.admissions.sat_scores.average.overall","latest.admissions.act_scores.midpoint.cumulative",
    "latest.cost.net_price.public","latest.cost.net_price.private","latest.cost.attendance.academic_year",
    "latest.cost.tuition.in_state","latest.cost.tuition.out_of_state",
    "latest.academics.program_percentage.agriculture","latest.academics.program_percentage.computer","latest.academics.program_percentage.engineering",
    "latest.academics.program_percentage.business_marketing","latest.academics.program_percentage.health","latest.academics.program_percentage.education",
    "latest.completion.rate_suppressed.overall",
    "latest.earnings.10_yrs_after_entry.median",
    "school.degrees_awarded.predominant",
])

class ScorecardClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, timeout: float = 30.0):
        self.api_key = api_key or API_KEY
        if not self.api_key:
            raise RuntimeError("COLLEGE_SCORECARD_API_KEY is not set")
        self.base_url = base_url or SCORECARD_BASE_URL
        self.timeout = timeout
        limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            limits=limits,
            http2=True,
            headers={"User-Agent": "EdPlanBackend/1.0"},
        )

    async def close(self):
        await self.client.aclose()

    def _retry_predicate(exc: BaseException) -> bool:
        # Retry network errors and 5xx; don't retry 4xx.
        if isinstance(exc, httpx.HTTPStatusError):
            return 500 <= exc.response.status_code < 600
        return True  # httpx.RequestError and others

    @retry(
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        stop=stop_after_attempt(3),
        retry=retry_if_exception(_retry_predicate),
    )
    async def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        # Inject API key and ensure JSON
        p = dict(params)
        p["api_key"] = self.api_key
        if not path.endswith(".json"):
            path = f"{path}.json"
        r = await self.client.get(path, params=p)
        r.raise_for_status()
        return r.json()

    async def ping(self) -> None:
        """Minimal upstream call used for readiness checks."""
        await self._get("schools", {"per_page": 1, "fields": "id"})

    async def autocomplete(self, q: str, per_page: int = 10) -> dict:
        params = {
            'per_page': min(max(per_page,1), 100),
            'fields': AUTOCOMPLETE_FIELDS,
            'school.name': f"~.*{q}.*",
            'sort': 'school.name:asc',
        }
        return await self._get('schools', params)

    async def search_schools(
        self,
        q: Optional[str] = None,
        state: Optional[str] = None,
        city: Optional[str] = None,
        ownership: Optional[int] = None,
        page: int = 0,
        per_page: int = 25,
        sort: Optional[str] = None,
        fields: Optional[str] = None,
    ) -> Dict[str, Any]:
        per_page = min(max(per_page, 1), 100)
        params: Dict[str, Any] = {
            "page": page,
            "per_page": per_page,
            "fields": fields or DEFAULT_FIELDS,
        }
        # Filters
        if q:
            # fuzzy regex match on name
            params["school.name"] = f"~.*{q}.*"
        if state:
            params["school.state"] = state.upper()
        if city:
            params["school.city"] = city
        if ownership is not None:
            # 1 = Public, 2 = Private nonprofit, 3 = Private for-profit
            params["school.ownership"] = ownership
        if sort:
            params["sort"] = sort  # e.g., latest.student.size:desc

        return await self._get("schools", params)

    async def get_school_details(self, unit_id: int, fields: Optional[str] = None) -> Dict[str, Any]:
        params = {
            "id": unit_id,
            "per_page": 1,
            "fields": fields or DETAIL_FIELDS,
        }
        return await self._get("schools", params)

    async def get_programs(self, unit_id: int, cip_prefix: Optional[str] = None, fields: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "id": unit_id,
            "per_page": 100,
            "fields": fields or PROGRAM_FIELDS,
        }
        if cip_prefix:
            # prefix like "11" for CS-related, or full "1107"
            params["latest.programs.cip_4_digit.code"] = f"{cip_prefix}*"
        return await self._get("schools", params)

    async def get_many(self, ids: List[int], fields: Optional[str] = None, per_page: int = 100) -> Dict[str, Any]:
        # Scorecard supports comma-separated id list up to some URL length; batch if needed.
        per_page = min(max(per_page, 1), 100)
        params = {
            "id": ",".join(map(str, ids)),
            "per_page": per_page,
            "fields": fields or DEFAULT_FIELDS,
        }
        return await self._get("schools", params)
