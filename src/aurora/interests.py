"""Built-in Aurora interest and research-field presets."""

from __future__ import annotations

from typing import Any


REPO_INTEREST_PRESETS: dict[str, dict[str, Any]] = {
    "ml": {
        "terms": ["machine learning", "deep learning", "ml", "pytorch", "tensorflow"],
        "languages": ["Python"],
    },
    "agents": {
        "terms": ["agent", "agentic", "llm agent", "tool use", "coding agent"],
        "languages": ["Python", "TypeScript"],
    },
    "cv": {
        "terms": ["computer vision", "vision", "opencv", "segmentation", "object detection"],
        "languages": ["Python"],
    },
    "nlp": {
        "terms": ["nlp", "natural language processing", "transformer", "llm", "rag"],
        "languages": ["Python"],
    },
    "rl": {
        "terms": ["reinforcement learning", "rl", "policy gradient", "gymnasium"],
        "languages": ["Python"],
    },
    "mlops": {
        "terms": ["mlops", "model serving", "feature store", "experiment tracking", "inference"],
        "languages": ["Python", "Go"],
    },
    "devtools": {
        "terms": ["developer tool", "cli", "sdk", "code generation", "automation"],
        "languages": ["TypeScript", "Python", "Go", "Rust"],
    },
    "mcp": {
        "terms": ["mcp", "model context protocol", "mcp server"],
        "languages": ["TypeScript", "Python"],
    },
    "workflow-automation": {
        "terms": ["workflow", "automation", "orchestration", "scheduler"],
        "languages": ["TypeScript", "Python", "Go"],
    },
}


SCHOLAR_FIELD_PRESETS: dict[str, dict[str, list[str]]] = {
    "ml": {
        "categories": ["cs.LG", "cs.AI", "stat.ML"],
        "keywords": ["machine learning", "representation learning", "optimization"],
        "venues": ["ICML", "NeurIPS", "ICLR", "AISTATS", "COLT", "UAI", "MLSys", "TMLR"],
    },
    "agents": {
        "categories": ["cs.AI", "cs.CL"],
        "keywords": ["llm agents", "tool use", "reasoning", "planning", "agentic"],
        "venues": ["ICLR", "NeurIPS", "ICML", "ACL", "EMNLP", "NAACL"],
    },
    "cv": {
        "categories": ["cs.CV", "cs.LG"],
        "keywords": ["computer vision", "object detection", "segmentation", "vision-language"],
        "venues": ["CVPR", "ICCV", "ECCV", "NeurIPS", "ICLR", "ICML"],
    },
    "nlp": {
        "categories": ["cs.CL", "cs.AI"],
        "keywords": ["natural language processing", "large language models", "retrieval augmented generation"],
        "venues": ["ACL", "EMNLP", "NAACL", "ICLR", "NeurIPS"],
    },
    "rl": {
        "categories": ["cs.LG", "cs.AI", "cs.RO"],
        "keywords": ["reinforcement learning", "policy optimization", "offline rl", "reward modeling"],
        "venues": ["ICML", "NeurIPS", "ICLR", "CoRL"],
    },
    "systems": {
        "categories": ["cs.DC", "cs.SE", "cs.PL", "cs.SY"],
        "keywords": ["distributed systems", "compiler", "runtime", "systems for machine learning"],
        "venues": ["SOSP", "OSDI", "NSDI", "EuroSys", "MLSys"],
    },
    "alignment": {
        "categories": ["cs.AI", "cs.CL", "cs.CY"],
        "keywords": ["alignment", "safety", "preference learning", "rlhf", "constitutional ai"],
        "venues": ["NeurIPS", "ICLR", "ICML", "ACL"],
    },
    "multimodal": {
        "categories": ["cs.CV", "cs.CL", "cs.AI"],
        "keywords": ["multimodal learning", "vision-language", "audio-language", "video understanding"],
        "venues": ["CVPR", "ICCV", "ECCV", "ACL", "NeurIPS", "ICLR"],
    },
}


def clean_preset_names(values: list[str], presets: dict[str, Any], *, label: str) -> list[str]:
    """Normalize and validate preset names while preserving user order."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip().lower()
        if not value or value in seen:
            continue
        if value not in presets:
            available = ", ".join(sorted(presets))
            raise ValueError(f"unknown {label}: {value}; available: {available}")
        cleaned.append(value)
        seen.add(value)
    if not cleaned:
        raise ValueError(f"at least one {label} is required")
    return cleaned


def unique_text(values: list[str]) -> list[str]:
    """Trim string values, preserve order, and remove case-insensitive duplicates."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        key = value.lower()
        if not value or key in seen:
            continue
        cleaned.append(value)
        seen.add(key)
    return cleaned
