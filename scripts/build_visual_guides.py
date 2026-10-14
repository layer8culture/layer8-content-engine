"""Rebuild five guides from the verified export, never from the legacy site.

The current deployment mixes static guides and Next exports. Guide documents are
progressively enhanced HTML; the exact current shared shell and its CSS are reused.
Non-guide files and the original Next runtime remain untouched.
"""
from __future__ import annotations

import argparse
import html
import re
import subprocess
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

BASELINE = "b7423550086bbfadd38a98b71e96ff3060fc27f8"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}


@dataclass
class Node:
    tag: str
    attrs: dict
    start: int
    end: int = 0
    children: list[Node] = field(default_factory=list)


class Document(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.offsets = [0]
        for line in source.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.root = Node("document", {}, 0, len(source))
        self.stack = [self.root]
        self.feed(source)

    def source_offset(self) -> int:
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.source_offset())
        self.stack[-1].children.append(node)
        if tag in VOID:
            node.end = node.start + len(self.get_starttag_text())
        else:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if self.stack[-1].tag != tag:
            raise ValueError(f"Unbalanced {tag} at {self.getpos()}")
        node = self.stack.pop()
        node.end = self.source_offset() + len(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(
            tag, dict(attrs), self.source_offset(),
            self.source_offset() + len(self.get_starttag_text()),
        ))

    def find(self, tag: str, node: Node | None = None) -> list[Node]:
        parent = node or self.root
        return [child for child in parent.children if child.tag == tag] + [
            found for child in parent.children for found in self.find(tag, child)
        ]

    def raw(self, node: Node) -> str:
        return self.source[node.start:node.end]

    def text(self, node: Node) -> str:
        return html.unescape(re.sub(r"<[^>]+>", "", self.raw(node))).strip()


# label, takeaway, builder move, diagram labels
GUIDES = {
    "ai-builder-workflow-map": ("workflow", "Eight decisions. One accountable system.", [
        ("Outcome", "Define what done looks like before choosing a tool.", "Name the result, audience, quality bar, and deadline.", ["Repeated task", "Clear outcome", "Quality bar"]),
        ("Context", "Relevant context beats a mountain of information.", "Separate sources of truth from optional references.", ["Sources", "Useful context", "Boundaries"]),
        ("Tasks", "Small, concrete tasks are easier to inspect.", "Give each task an input, output, and done condition.", ["Receive", "Validate", "Deliver"]),
        ("Roles & tools", "Match capability to the task, not hype to the workflow.", "Give every task one responsible tool, agent, or person.", ["Task", "Right tool", "Named owner"]),
        ("Guardrails", "Permissions are part of the design.", "Require approval before public publishing or other risky actions.", ["Proposed action", "Permission gate", "Allowed action"]),
        ("Handoffs", "The next step should never have to guess.", "Pass a named artifact, schema, or checklist.", ["Task output", "Named artifact", "Next task"]),
        ("Review & ship", "Automation makes review easier. It does not remove accountability.", "Make the decision visible: approved, changes requested, or stopped.", ["Draft result", "Human review", "Approved ship"]),
        ("Learn & reuse", "Save what changed the result, not every token.", "Choose one measure of quality, speed, cost, or business impact.", ["Run results", "Measure + learn", "Better next run"]),
    ]),
    "mcp-explained-without-the-hype": ("mcp", "A connection contract. Not a permission slip.", [
        ("The connection", "The host contains a client. The server exposes capabilities.", "Name the app, connector, and service in your own workflow.", ["AI host", "MCP client", "MCP server"]),
        ("Capabilities", "Tools act. Resources inform. Prompts guide.", "Pick only the capability your repeated task needs.", ["Tool", "Resource", "Prompt"]),
        ("When it fits", "Repeated tool access is the reason to connect.", "Use chat for one-off answers; connect one useful workflow first.", ["Repeated task", "Shared contract", "Useful access"]),
        ("Start safely", "One read-only integration is a better first win.", "Define predictable inputs, outputs, authentication, and logs.", ["Read-only", "Test + inspect", "Controlled rollout"]),
        ("Permissions", "Tool access changes the risk of the work.", "Authorize per capability and require human review before writes.", ["Least privilege", "Human approval", "Audit trail"]),
    ]),
    "build-your-ai-org-chart": ("org", "Accountable roles. Explicit decision rights.", [
        ("Chief of Staff", "Translate human priorities into coordinated work.", "Define the mandate, escalation path, and decisions that stay human.", ["Human owner", "Chief of Staff", "Priorities"]),
        ("Program Manager", "Coordinate related initiatives and dependencies.", "Make the cross-project handoff and operating cadence explicit.", ["Human owner", "Program Manager", "Dependencies"]),
        ("Project Manager", "Turn a defined outcome into inspectable delivery.", "Name the deliverables, done conditions, and review checkpoints.", ["Human owner", "Project Manager", "Delivery"]),
        ("Technical Architect", "Keep technical choices coherent with the outcome.", "Give the role clear constraints, artifacts, and decision boundaries.", ["Human owner", "Tech Architect", "Coherence"]),
    ]),
    "seven-prompting-habits-that-make-ai-useful": ("prompt", "A clear brief beats a clever phrase.", [
        ("Outcome", "Start with the result, not an open-ended request.", "Specify what useful output looks like and who will use it.", ["Vague ask", "Clear result"]),
        ("Context", "Bring the useful truth into the conversation.", "Include source material and distinguish facts from assumptions.", ["Missing facts", "Relevant context"]),
        ("Constraints", "Boundaries help the model make better choices.", "Name scope, format, length, and what must not change.", ["Anything goes", "Defined boundaries"]),
        ("Examples", "Show the quality bar instead of hoping it is understood.", "Provide a representative example and explain what makes it useful.", ["Guess the style", "Visible standard"]),
        ("Decomposition", "A big job becomes clearer when it is split.", "Ask for the next concrete step, not the whole project at once.", ["One giant ask", "Small useful tasks"]),
        ("Review", "Plausible is not the same as correct.", "Check claims against sources and outputs against the brief.", ["Plausible draft", "Verified result"]),
        ("Iteration", "Use feedback to improve the next attempt.", "Describe the gap precisely, revise, and save the working pattern.", ["Start over", "Feedback + reuse"]),
    ]),
    "ai-fluency-consumer-user-builder-operator": ("fluency", "Move from consuming answers to owning outcomes.", [
        ("Consumer", "Notice what AI produces and where its limits show up.", "Move beyond watching: try one real task and inspect the result.", ["Consumer", "User", "Builder", "Operator"]),
        ("User", "Use AI deliberately for a useful task.", "Bring context and a quality bar rather than accepting the first answer.", ["Consumer", "User", "Builder", "Operator"]),
        ("Builder", "Turn useful tasks into repeatable workflows.", "Define inputs, handoffs, tools, and a human review point.", ["Consumer", "User", "Builder", "Operator"]),
        ("Operator", "Own the reliability and consequences of the system.", "Monitor quality, failures, permissions, and the next improvement.", ["Consumer", "User", "Builder", "Operator"]),
    ]),
}


def baseline(path: str) -> str:
    return subprocess.check_output(["git", "show", f"{BASELINE}:{path}"]).decode("utf-8")


def box(x, y, width, label, active=False) -> str:
    return (f'<g class="fg-node{" fg-active" if active else ""}">'
            f'<rect x="{x}" y="{y}" width="{width}" height="58" rx="8"/>'
            f'<text x="{x + width / 2}" y="{y + 34}" text-anchor="middle">'
            f'{html.escape(label)}</text></g>')


def diagram(kind: str, index: int, label: str, nodes: list[str]) -> str:
    title = f"{label}: " + " / ".join(nodes)
    if kind == "org":
        art = box(221, 18, 158, "Human accountability", True)
        art += '<path d="M300 76V105H65V124 M300 105H535V124 M222 105V124 M378 105V124"/>'
        roles = ["Chief of Staff", "Program Manager", "Project Manager", "Tech Architect"]
        for i, role in enumerate(roles):
            art += box(5 + i * 151, 124, 137, role, i == index)
        art += '<text x="300" y="230" text-anchor="middle">Mandate + inputs + outputs + decision rights</text>'
    elif kind == "mcp":
        art = box(10, 35, 170, "AI host", True) + box(24, 100, 142, "MCP client")
        art += box(239, 75, 145, "MCP server", True)
        art += '<path d="M166 129H210V104H239 M384 104H421 M421 47V211"/>'
        for y, text in zip([18, 100, 182], ["Tools: actions", "Resources: context", "Prompts: patterns"]):
            art += box(430, y, 165, text, index == 1)
        art += '<text x="300" y="275" text-anchor="middle">Authentication + scoped authorization + human review</text>'
    elif kind == "fluency":
        art = '<path d="M28 252H572"/>'
        for i, text in enumerate(nodes):
            x, y = 10 + i * 150, 181 - i * 49
            art += box(x, y, 135, text, i == index)
            if i < 3:
                art += f'<path d="M{x + 135} {y + 29}h8v-49h7"/>'
        art += '<text x="300" y="278" text-anchor="middle">More ownership, not just more tools</text>'
    elif kind == "prompt":
        art = box(20, 91, 225, nodes[0]) + box(355, 91, 225, nodes[1], True)
        art += '<path d="M245 120H355 M466 149V221H132V149"/>'
        art += '<text x="300" y="250" text-anchor="middle">Brief → draft → inspect → refine</text>'
    else:
        art = "".join(box(10 + i * 202, 103, 176, text, i == 1) for i, text in enumerate(nodes))
        art += '<path d="M186 132H212 M388 132H414 M502 161V229H98V161"/>'
        art += '<text x="300" y="260" text-anchor="middle">Clear input. Visible output. Human control.</text>'
    return (f'<figure class="fg-diagram"><svg viewBox="0 0 610 300" role="img" '
            f'aria-label="{html.escape(title, quote=True)}">{art}'
            '<circle class="fg-packet" cx="300" cy="290" r="3"/></svg>'
            f'<figcaption>{html.escape(title)}</figcaption></figure>')


def build(slug: str, shell: Document) -> str:
    source = baseline(f"site/guides/{slug}/index.html")
    original = Document(source)
    main = original.find("main")[0]
    sections = [node for node in main.children if node.tag == "section"]
    hero = sections[0]
    title = original.text(original.find("h1", hero)[0])
    kind, takeaway, topics = GUIDES[slug]
    head = re.search(r"<head>(.*?)</head>", source, re.S).group(1)
    # Keep metadata, JSON-LD and fonts; an old guide's inline CSS must not style the shell.
    head = re.sub(r"<style\b[^>]*>.*?</style>", "", head, flags=re.S)
    head = re.sub(r"<script\b(?![^>]*application/ld\+json)[^>]*>.*?</script>", "", head, flags=re.S)
    head = re.sub(r'<link\b[^>]*(?:as="script"|rel="modulepreload")[^>]*>', "", head)
    head = re.sub(r'<link\b[^>]*href="/styles\.css"[^>]*>', "", head)
    head = "\n".join(line.rstrip() for line in head.splitlines())
    css = '<link rel="stylesheet" href="/_next/static/chunks/07qtc9k-t8t4j.css" data-precedence="next"/>'
    if "07qtc9k-t8t4j.css" not in head:
        head += css
    head += '<link rel="stylesheet" href="/guides/field-guide.css"/>'
    hero_markup = original.raw(hero)
    hero_markup = re.sub(r'<section\b[^>]*>', '<section class="fg-hero">', hero_markup, count=1)
    hero_markup = re.sub(r'<div aria-hidden="true"[^>]*></div>', "", hero_markup)
    cards, panels = [], []
    for index, (label, lead, move, labels) in enumerate(topics):
        identity = f"topic-{index + 1}"
        deeper = (f"stage-{index + 1}" if kind == "workflow" else
                  "reference-5" if kind == "org" else
                  f"reference-{index + 1}" if kind == "mcp" else "reference-1")
        cards.append(
            f'<a class="fg-card" href="#{identity}" aria-controls="{identity}">'
            f'<span class="fg-number">{index + 1:02d} <span aria-hidden="true">↗</span></span>'
            f'<strong>{html.escape(label)}</strong><span>{html.escape(lead)}</span></a>'
        )
        panels.append(
            f'<section id="{identity}" class="fg-panel" aria-labelledby="{identity}-title">'
            '<div class="fg-copy">'
            f'<span class="font-eyebrow text-layer8-blue-soft">{index + 1:02d} / {html.escape(label)}</span>'
            f'<h2 id="{identity}-title" tabindex="-1">{html.escape(label)}</h2>'
            f'<p>{html.escape(lead)}</p><div class="fg-move"><span class="font-eyebrow">Your builder move</span>'
            f'<p>{html.escape(move)}</p></div>'
            f'<a class="fg-deeper" href="#{deeper}">Full explanation and resources ↓</a></div>'
            f'{diagram(kind, index, label, labels)}</section>'
        )
    reference = []
    for index, section in enumerate(sections[1:]):
        headings = original.find("h2", section)
        label = original.text(headings[0]) if headings else "Key context"
        reference.append(
            f'<details class="fg-reference" id="reference-{index + 1}">'
            f'<summary>{html.escape(label)}<span aria-hidden="true">+</span></summary>'
            f'<div class="fg-original">{original.raw(section)}</div></details>'
        )
    # MCP's summary cards sit outside sections in the original static page.
    extras = [node for node in main.children if node.tag == "div"]
    for node in extras:
        reference.insert(0, '<details class="fg-reference"><summary>Key context'
                         '<span aria-hidden="true">+</span></summary>'
                         f'<div class="fg-original">{original.raw(node)}</div></details>')
    article_schema = "".join(original.raw(node) for node in main.children
                             if node.tag == "script" and node.attrs.get("type") == "application/ld+json")
    body = (
        '<main id="guide" class="fg" data-visual-guide>'
        f'{article_schema}{hero_markup}'
        '<div class="fg-wrap"><div class="fg-overview-heading"><div>'
        '<span class="font-eyebrow text-layer8-blue-soft">The big picture</span>'
        f'<h2>{html.escape(takeaway)}</h2></div>'
        '<button class="fg-motion" type="button" aria-pressed="false" hidden>Pause motion</button></div>'
        f'<nav class="fg-cards" aria-label="{html.escape(title)} topics">{"".join(cards)}</nav>'
        '<p class="fg-hint">Select a topic to unpack it. Keep the full reference close.</p>'
        f'<div class="fg-panels">{"".join(panels)}</div>'
        '<div class="fg-controls" hidden><button type="button" data-previous>← Previous topic</button>'
        '<span class="fg-position" aria-live="polite"></span>'
        '<button type="button" data-next>Next topic →</button></div>'
        '<section id="full-reference" class="fg-resources"><div class="fg-resource-heading">'
        '<span class="font-eyebrow text-layer8-blue-soft">Keep going</span>'
        '<h2>The complete field reference.</h2>'
        '<p>All original explanations, checklists, prompts, and reading links. Open only what you need.</p>'
        '</div>' + "".join(reference) +
        '</section><p class="fg-back"><a href="/guides/">← All AI Field Guides</a></p></div></main>'
    )
    nav = shell.raw(shell.find("nav")[0])
    footer = shell.raw(shell.find("footer")[0])
    html_start = re.search(r"<html\b[^>]*>", shell.source).group(0)
    body_start = re.search(r"<body\b[^>]*>", shell.source).group(0)
    return (f'<!DOCTYPE html>{html_start}<head>{head}</head>{body_start}'
            '<a class="fg-skip" href="#guide">Skip to guide</a>'
            f'{nav}{body}{footer}<script src="/guides/field-guide.js" defer></script></body></html>')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("site"))
    args = parser.parse_args()
    shell = Document(baseline("site/index.html"))
    for slug in GUIDES:
        target = args.output / "guides" / slug / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(build(slug, shell), encoding="utf-8", newline="\n")
        print(f"Built {target}")


if __name__ == "__main__":
    main()
