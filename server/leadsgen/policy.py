"""Explicit business rules, versioned independently of evidence extraction."""

CADENCE_DAYS = (0, 7, 14, 28, 60, 90)
POLICY_VERSION = "2026-09-23-broad-business-entry-v2"
ROLE_NAMES = {
    "procurement": "采购 / 供应商入口",
    "partnership": "商务合作入口",
    "sales": "销售入口 · 可代为转交",
    "support": "客服入口 · 可代为转交",
    "general": "通用业务入口",
    "business": "公开联系人 · 职务未核实",
    "excluded": "非业务联系用途",
}


def email_role(email: str) -> str:
    local = email.split("@", 1)[0].lower()
    parts = set(local.replace("-", ".").replace("_", ".").split("."))
    if local.replace("-", "").replace("_", "") == "noreply" or parts & {
        "noreply",
        "abuse",
        "privacy",
        "dpo",
        "legal",
        "careers",
        "jobs",
        "hr",
    }:
        return "excluded"
    for role, names in (
        ("procurement", {"procurement", "purchase", "purchasing", "sourcing", "vendors", "buying"}),
        ("partnership", {"partners", "partnership", "partnerships", "business", "bd"}),
        ("sales", {"sales", "marketing", "commercial"}),
        ("support", {"support", "service", "help", "helpdesk", "customerservice", "care"}),
        ("general", {"info", "contact", "hello", "enquiries", "inquiries", "office"}),
    ):
        if parts & names:
            return role
    return "business"


def contact_order(email: str) -> tuple[int, str]:
    order = ["procurement", "partnership", "sales", "general", "business", "support", "excluded"]
    return order.index(email_role(email)), email.casefold()
