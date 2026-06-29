"""
FORGE Dynamic Agent Factory
============================
Dynamically spawns specialized agents based on plan step requirements.

Supports up to 20+ specialized agent roles, automatically matching step
skills and descriptions to target behaviors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ─────────────────────────────────────────────
#  Specialized Agent Roles (20+ Definitions)
# ─────────────────────────────────────────────

SPECIALIZED_ROLES: dict[str, dict[str, str]] = {
    "python_specialist": {
        "role_name": "Python Specialist",
        "description": "Expert in writing, refactoring, and optimizing Python code and unit tests.",
    },
    "javascript_specialist": {
        "role_name": "JavaScript Specialist",
        "description": "Expert in Node.js, frontend JavaScript frameworks, and script development.",
    },
    "web_scraper": {
        "role_name": "Web Scraper Specialist",
        "description": "Expert in crawling, parsing HTML, and extracting structured data from web pages.",
    },
    "db_consultant": {
        "role_name": "Database Consultant",
        "description": "Expert in designing, querying, and optimizing relational databases (SQLite, PostgreSQL).",
    },
    "seo_auditor": {
        "role_name": "SEO Auditor",
        "description": "Expert in search engine optimization, meta-tag analysis, and web layout optimization.",
    },
    "css_designer": {
        "role_name": "CSS/HTML Designer",
        "description": "Expert in responsive design, animations, typography, and visual aesthetics.",
    },
    "devops_engineer": {
        "role_name": "DevOps & Build Engineer",
        "description": "Expert in environment setup, package management, dependency updates, and building installers.",
    },
    "security_inspector": {
        "role_name": "Security & Safety Inspector",
        "description": "Expert in evaluating permission escalations, vulnerability analysis, and payload scanning.",
    },
    "git_manager": {
        "role_name": "Git & Repository Manager",
        "description": "Expert in version control, staging changes, writing structured commit messages, and pushes.",
    },
    "wordpress_expert": {
        "role_name": "WordPress Architect",
        "description": "Expert in WordPress core, plugins, social media integrations, and API publishers.",
    },
    "critic_auditor": {
        "role_name": "Critic Auditor",
        "description": "Specialized reviewer focused on verification, result validation, and checking for errors.",
    },
    "shell_operator": {
        "role_name": "Shell Operator",
        "description": "Expert in running CLI utilities, diagnostics, and parsing command-line outputs.",
    },
    "markdown_writer": {
        "role_name": "Markdown & Report Writer",
        "description": "Expert in writing documentation, logs, reports, and structuring markdown files.",
    },
    "configs_expert": {
        "role_name": "Configuration Specialist",
        "description": "Expert in reading/writing YAML, TOML, JSON configuration files and settings.",
    },
    "network_diagnostician": {
        "role_name": "Network Diagnostician",
        "description": "Expert in testing connections, API endpoints, ping requests, and URL lookups.",
    },
    "math_reasoner": {
        "role_name": "Mathematical Reasoner",
        "description": "Expert in numerical calculations, statistics, algorithms, and complexity analysis.",
    },
    "creative_writer": {
        "role_name": "Creative Assistant",
        "description": "Expert in writing copy, hooks, marketing descriptions, and product branding.",
    },
    "api_integrator": {
        "role_name": "API Integrator",
        "description": "Expert in integrating external REST/GraphQL APIs, payloads, and headers.",
    },
    "bug_hunter": {
        "role_name": "Bug Hunter",
        "description": "Expert in code debugging, reading traceback logs, and implementing robust error handling.",
    },
    "generalist": {
        "role_name": "Generalist Assistant",
        "description": "Default agent role for general assistant tasks and reasoning steps.",
    },
}


@dataclass(slots=True)
class DynamicAgentSpec:
    agent_id: str
    role_name: str
    description: str
    instructions: list[str]


class AgentFactory:
    """
    Factory for analyzing steps and spawning dynamic specialized agents.
    """

    @classmethod
    def spawn_agent_for_step(cls, step_id: str, skill_name: str | None, action: str) -> DynamicAgentSpec:
        """
        Dynamically analyzes the plan step and returns a tailored agent spec.
        """
        skill = (skill_name or "").lower().strip()
        desc = action.lower()

        # 1. Content keyword checks first (highly specific behavior overrides generic skills)
        if any(kw in desc for kw in ("bug", "fix", "error", "traceback", "exception")):
            role = "bug_hunter"
        elif any(kw in desc for kw in ("calculate", "math", "equation", "sum", "factorial")):
            role = "math_reasoner"
        elif any(kw in desc for kw in ("api", "headers", "payload", "graphql")):
            role = "api_integrator"
        elif "critic" in skill or "review" in desc:
            role = "critic_auditor"
        elif skill == "github-publisher":
            role = "git_manager"
        elif skill == "wordpress-publisher":
            role = "wordpress_expert"
        
        # 2. General skill matching
        elif skill == "file-editor":
            if any(kw in desc for kw in ("python", "test_")):
                role = "python_specialist"
            elif any(kw in desc for kw in ("js", "javascript", "node")):
                role = "javascript_specialist"
            elif any(kw in desc for kw in ("css", "html", "style", "design")):
                role = "css_designer"
            elif any(kw in desc for kw in ("md", "txt", "readme", "changelog")):
                role = "markdown_writer"
            elif any(kw in desc for kw in ("toml", "yaml", "yml", "json", "config")):
                role = "configs_expert"
            else:
                role = "python_specialist"
        elif skill == "shell-executor":
            if any(kw in desc for kw in ("pip", "npm", "build", "dist", "install")):
                role = "devops_engineer"
            elif any(kw in desc for kw in ("git", "commit", "push")):
                role = "git_manager"
            elif any(kw in desc for kw in ("db", "sqlite", "sql")):
                role = "db_consultant"
            elif any(kw in desc for kw in ("ping", "curl", "wget", "port")):
                role = "network_diagnostician"
            else:
                role = "shell_operator"
        elif skill == "browser-executor":
            if any(kw in desc for kw in ("scrap", "extract", "crawl", "html")):
                role = "web_scraper"
            elif any(kw in desc for kw in ("seo", "meta", "keywords")):
                role = "seo_auditor"
            else:
                role = "web_scraper"
        else:
            role = "generalist"

        spec_data = SPECIALIZED_ROLES.get(role, SPECIALIZED_ROLES["generalist"])
        
        # Build dynamic custom instructions for this specific step
        instructions = [
            f"You are the {spec_data['role_name']}.",
            f"Role purpose: {spec_data['description']}",
            f"Assigned task: {action}",
            "Focus only on your specialization during this step.",
            "Collaborate with the Critic agent by producing highly verified results.",
        ]

        # Add role-specific warnings/instructions
        if role == "python_specialist":
            instructions.append("Ensure Python syntax is valid (strict syntax check).")
        elif role == "security_inspector":
            instructions.append("Highlight any potential safety risks or sensitive operations.")
        elif role == "critic_auditor":
            instructions.append("Be extra precise. Verify every metric and test status.")

        return DynamicAgentSpec(
            agent_id=f"agent:{role}:{step_id}",
            role_name=spec_data["role_name"],
            description=spec_data["description"],
            instructions=instructions,
        )

    @classmethod
    def list_roles(cls) -> list[dict[str, str]]:
        """List all available specialized roles."""
        return [
            {"id": key, "name": val["role_name"], "description": val["description"]}
            for key, val in SPECIALIZED_ROLES.items()
        ]
