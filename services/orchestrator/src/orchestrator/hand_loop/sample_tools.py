"""Two read-only tools over a tiny sample dataset.

Sample data for Day 3 only. Real CVE and KEV data arrive on Day 16.
"""

from typing import Annotated

from anthropic.lib.tools import ToolError
from pydantic import BaseModel, ConfigDict, Field

from .tools import LocalTool

CveId = Annotated[
    str,
    Field(pattern=r"^CVE-\d{4}-\d{4,}$", description="A CVE ID, such as CVE-2021-44228"),
]


class CveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cve_id: CveId


# CVE ID: (summary, CVSS v3 base score, fix)
SAMPLE_CVES: dict[str, tuple[str, str, str]] = {
    "CVE-2021-44228": (
        "Log4Shell: remote code execution in Apache Log4j 2 through JNDI lookups in logged text",
        "10.0",
        "Upgrade Log4j to 2.17.1 or later",
    ),
    "CVE-2022-22965": (
        "Spring4Shell: remote code execution in Spring Framework data binding on JDK 9 or later",
        "9.8",
        "Upgrade Spring Framework to 5.3.18 or 5.2.20 or later",
    ),
    "CVE-2099-0001": (
        "A made-up flaw used in tests. It is not real.",
        "4.3",
        "None needed",
    ),
}

# CVE ID: the date CISA added it to the Known Exploited Vulnerabilities catalog
SAMPLE_KEV: dict[str, str] = {
    "CVE-2021-44228": "2021-12-10",
    "CVE-2022-22965": "2022-04-04",
}


async def lookup_cve(cve_id: CveId) -> str:
    if cve_id not in SAMPLE_CVES:
        raise ToolError(f"No record for {cve_id} in the sample data.")
    summary, score, fix = SAMPLE_CVES[cve_id]
    return f"{cve_id}: {summary}. CVSS {score}. Fix: {fix}."


async def check_kev(cve_id: CveId) -> str:
    if cve_id in SAMPLE_KEV:
        return (
            f"{cve_id} is in the sample KEV list (added {SAMPLE_KEV[cve_id]}): "
            "attackers are known to exploit it."
        )
    return f"{cve_id} is not in the sample KEV list."


LOOKUP_CVE = LocalTool(
    name="lookup_cve",
    description=(
        "Describe a known security flaw: what it is, its CVSS severity score and the fix. "
        "Use this to explain a CVE."
    ),
    input_model=CveInput,
    func=lookup_cve,
)
CHECK_KEV = LocalTool(
    name="check_kev",
    description=(
        "Check whether attackers are known to be exploiting a CVE, using CISA's Known Exploited "
        "Vulnerabilities list. A listed flaw should usually be fixed first."
    ),
    input_model=CveInput,
    func=check_kev,
)
SAMPLE_TOOLS = (LOOKUP_CVE, CHECK_KEV)
