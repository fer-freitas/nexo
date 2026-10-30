"""Scanners sem IA: Terraform, contratos, configuração e C#. Foco em .NET, Kafka, SQS, SNS, ECS e Lambda.

Cada achado tem fonte, arquivo, linha e confiança. O que não dá para resolver vira "não resolvido", sem chute.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from nexo.modules.lineage.domain.model import normalize

SCANNER_VERSION = 1


@dataclass
class Finding:
    direction: str
    kind: str  # kafka | sns | sqs | http
    name: str
    file: str
    line: int
    source: str
    confidence: str
    message: str = ""
    component: str = ""
    runtime: str = ""


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    components: dict[str, str] = field(default_factory=dict)  # nome → runtime


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


RESOURCE = re.compile(r'resource\s+"(aws_[a-z0-9_]+)"\s+"([\w-]+)"\s*\{', re.M)
ATTR = re.compile(r"^\s*([a-z_]+)\s*=\s*(.+?)\s*$", re.M)
REF = re.compile(r"\b(aws_[a-z0-9_]+)\.([\w-]+)\.\w+")


def _blocks(text: str) -> list[tuple[str, str, str, int]]:
    out = []
    for m in RESOURCE.finditer(text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        out.append((m.group(1), m.group(2), text[m.end() : i - 1], m.start()))
    return out


def _attrs(body: str) -> dict[str, str]:
    return {k: v.strip().strip('"') for k, v in ATTR.findall(body)}


def scan_terraform(files: dict[str, str], result: ScanResult) -> None:
    resources: dict[tuple[str, str], dict[str, str]] = {}
    blocks = []
    for path, text in files.items():
        for kind, name, body, pos in _blocks(text):
            attrs = _attrs(body)
            resources[(kind, name)] = attrs
            blocks.append((path, kind, name, attrs, _line(text, pos)))

    def real_name(ref: str) -> str:
        m = REF.search(ref)
        if m:
            attrs = resources.get((m.group(1), m.group(2)), {})
            return attrs.get("name") or attrs.get("function_name") or m.group(2)
        return ref

    def literal(value: str) -> bool:
        return "${" not in value and "var." not in value and "local." not in value

    def channel_of(ref: str) -> tuple[str, str]:
        m = REF.search(ref)
        if m:
            kind = {"aws_sns_topic": "sns", "aws_sqs_queue": "sqs", "aws_msk_cluster": "kafka"}.get(m.group(1), "")
            return kind, real_name(ref)
        for kind, marker in (("sns", ":sns:"), ("sqs", ":sqs:"), ("kafka", ":kafka:")):
            if marker in ref:
                return kind, ref
        return "", ref

    for path, kind, _name, attrs, line in blocks:
        if kind == "aws_ecs_service":
            result.components[real_name(attrs.get("name", _name))] = "ecs"
        elif kind == "aws_lambda_function":
            result.components[attrs.get("function_name", _name)] = "lambda"
        elif kind == "aws_lambda_event_source_mapping":
            source_kind, source = channel_of(attrs.get("event_source_arn", ""))
            function = real_name(attrs.get("function_name", ""))
            topics = re.findall(r'"([^"]+)"', attrs.get("topics", ""))
            if topics:
                for topic in topics:
                    result.findings.append(
                        Finding(
                            "consumes",
                            "kafka",
                            topic,
                            path,
                            line,
                            "terraform",
                            "high",
                            component=function,
                            runtime="lambda",
                        )
                    )
            elif source_kind and literal(source):
                result.findings.append(
                    Finding(
                        "consumes",
                        source_kind,
                        source,
                        path,
                        line,
                        "terraform",
                        "high",
                        component=function,
                        runtime="lambda",
                    )
                )
            else:
                result.unresolved.append({"file": path, "line": line, "reason": "gatilho de Lambda sem nome resolvido"})
        elif kind == "aws_sns_topic_subscription":
            _k, topic = channel_of(attrs.get("topic_arn", ""))
            target_kind, target = channel_of(attrs.get("endpoint", ""))
            if literal(topic) and target_kind == "sqs":
                # a fila assina o tópico: quem consome a fila recebe o tópico
                result.findings.append(
                    Finding("subscribes", "sns", topic, path, line, "terraform", "high", component=f"sqs:{target}")
                )
            elif not literal(topic):
                result.unresolved.append({"file": path, "line": line, "reason": "assinatura SNS sem nome resolvido"})


def _load_config(text: str, path: str) -> dict[str, Any]:
    try:
        return dict(json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.M)))
    except ValueError:
        return {}


def flatten(data: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            out.update(flatten(v, f"{prefix}{k}:"))
    elif isinstance(data, str):
        out[prefix.rstrip(":")] = data
    return out


CONST = re.compile(r'\bconst\s+string\s+(\w+)\s*=\s*"([^"]+)"')
CONFIG_KEY = re.compile(
    r'(?:\[\s*"([\w:.\-]+)"\s*\]|GetValue<string>\(\s*"([\w:.\-]+)"\s*\)|GetSection\(\s*"([\w:.\-]+)"\s*\))'
)
SECRET_KEY = re.compile(r"(password|secret|token|connectionstring|apikey|api_key)", re.I)

PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("publishes", "kafka", re.compile(r"\.ProduceAsync\(\s*([^,)\s]+)")),
    ("publishes", "kafka", re.compile(r"\.Produce\(\s*([^,)\s]+)")),
    ("consumes", "kafka", re.compile(r"\.Subscribe\(\s*([^)]+?)\s*\)")),
    ("publishes", "sns", re.compile(r"TopicArn\s*=\s*([^,;}\s]+)")),
    ("publishes", "sqs", re.compile(r"new\s+SendMessage(?:Batch)?Request[^;]*?QueueUrl\s*=\s*([^,;}\s]+)", re.S)),
    ("publishes", "sqs", re.compile(r"\.SendMessageAsync\(\s*([^,)\s]+)")),
    ("consumes", "sqs", re.compile(r"new\s+ReceiveMessageRequest[^;]*?QueueUrl\s*=\s*([^,;}\s]+)", re.S)),
    ("consumes", "sqs", re.compile(r"\.ReceiveMessageAsync\(\s*([^,)\s]+)")),
)
ROUTES = (
    re.compile(r'\[\s*Http(?:Get|Post|Put|Patch|Delete)\s*\(\s*"([^"]*)"'),
    re.compile(r'\.Map(?:Get|Post|Put|Patch|Delete)\s*\(\s*"([^"]+)"'),
)
LAMBDA_EVENTS = {"SQSEvent": "sqs", "SNSEvent": "sns", "KafkaEvent": "kafka"}
HTTP_URL = re.compile(r"https?://([\w.\-]+)")


def scan_csharp(files: dict[str, str], config: dict[str, str], result: ScanResult, component_of: Any) -> None:
    constants: dict[str, str] = {}
    for text in files.values():
        constants.update(CONST.findall(text))

    def resolve(expr: str) -> tuple[str, bool]:
        value = expr.strip().rstrip(",")
        if value.startswith('"') and value.endswith('"') and "{" not in value:
            return value.strip('"'), True
        if value.startswith('$"') or "+" in value:
            return value, False
        short = value.split(".")[-1]
        if short in constants:
            return constants[short], True
        match = CONFIG_KEY.search(value)
        if match:
            key = next(g for g in match.groups() if g)
            if not SECRET_KEY.search(key) and key in config:
                return config[key], True
            return value, False
        tail = short.lstrip("_").lower()
        for key, cfg in sorted(config.items()):
            if SECRET_KEY.search(key):
                continue
            if key.split(":")[-1].lower().replace("_", "") in (tail, tail.replace("arn", "").replace("url", "")):
                return cfg, True
        return value, False

    for path, text in sorted(files.items()):
        component = component_of(path)
        for direction, kind, pattern in PATTERNS:
            for m in pattern.finditer(text):
                expr = m.group(1)
                items = (
                    re.findall(r'"([^"]+)"', expr)
                    if kind == "kafka" and direction == "consumes" and "{" in expr
                    else [expr]
                )
                for item in items:
                    raw = f'"{item}"' if items != [expr] else item
                    name, ok = resolve(raw)
                    line = _line(text, m.start())
                    if ok:
                        result.findings.append(
                            Finding(direction, kind, name, path, line, "code", "medium", component=component)
                        )
                    else:
                        result.unresolved.append(
                            {"file": path, "line": line, "reason": f"{kind}: nome montado em tempo de execução"}
                        )
        for pattern in ROUTES:
            for m in pattern.finditer(text):
                result.findings.append(
                    Finding(
                        "exposes",
                        "http",
                        component,
                        path,
                        _line(text, m.start()),
                        "code",
                        "medium",
                        component=component,
                    )
                )
                break
        for event, kind in LAMBDA_EVENTS.items():
            if re.search(rf"\b{event}\b\s+\w+", text):
                result.components[component] = "lambda"
                if not any(
                    f.component == component and f.direction == "consumes" and f.kind == kind for f in result.findings
                ):
                    result.unresolved.append(
                        {
                            "file": path,
                            "line": _line(text, text.index(event)),
                            "reason": f"handler de {event}: gatilho não visto no Terraform",
                        }
                    )


def scan_config(files: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for path, text in sorted(files.items()):
        merged.update(flatten(_load_config(text, path)))
    return merged


def scan_contract(path: str, text: str, result: ScanResult, component: str) -> None:
    """AsyncAPI 2 e 3. Canal e mensagem são a fonte de maior confiança, porque o time escreveu."""
    try:
        data = YAML(typ="safe", pure=True).load(text) or {}
    except YAMLError:
        result.unresolved.append({"file": path, "line": 1, "reason": "contrato ilegível"})
        return
    if not isinstance(data, dict) or "asyncapi" not in data:
        return
    version = str((data.get("info") or {}).get("version") or "")
    major = version.split(".")[0] if version else ""

    def kind_of(channel: dict[str, Any], name: str) -> str:
        bindings = channel.get("bindings") or {}
        for key in ("kafka", "sns", "sqs"):
            if key in bindings:
                return key
        return "kafka"

    def message(item: dict[str, Any]) -> str:
        msg = item.get("message") or {}
        if isinstance(msg, dict):
            name = msg.get("name") or str(msg.get("$ref", "")).rsplit("/", 1)[-1]
            return f"{name}@{major}" if name and major else str(name or "")
        return ""

    channels = data.get("channels") or {}
    for name, channel in channels.items():
        if not isinstance(channel, dict):
            continue
        address = str(channel.get("address") or name)
        for side, direction in (("publish", "consumes"), ("subscribe", "publishes")):
            # na v2, `publish` é o que os outros publicam para este serviço consumir
            if side in channel:
                result.findings.append(
                    Finding(
                        direction,
                        kind_of(channel, name),
                        address,
                        path,
                        1,
                        "contract",
                        "high",
                        message(channel[side]),
                        component,
                    )
                )
    for op in (data.get("operations") or {}).values():
        if not isinstance(op, dict):
            continue
        ref = str((op.get("channel") or {}).get("$ref", "")).rsplit("/", 1)[-1]
        channel = channels.get(ref) or {}
        if channel:
            direction = "publishes" if op.get("action") == "send" else "consumes"
            address = str(channel.get("address") or ref)
            msgs = channel.get("messages") or {}
            msg = next(iter(msgs), "")
            label = f"{msg}@{major}" if msg and major else ""
            result.findings.append(
                Finding(direction, kind_of(channel, ref), address, path, 1, "contract", "high", label, component)
            )


PROJECT_SDK = re.compile(r'<Project\s+Sdk="([^"]+)"')
TEST_NAME = re.compile(r"(?:^|[./\\])(tests?|\w*\.tests?|\w*specs?)(?:[./\\]|$)", re.I)
ENTRY = re.compile(
    r"\b(WebApplication\.CreateBuilder|Host\.CreateDefaultBuilder|Host\.CreateApplicationBuilder|LambdaBootstrap|ILambdaContext)\b"
)


def _open(unresolved: list[dict[str, Any]], declared: dict[str, Any] | None) -> list[dict[str, Any]]:
    confirmed = set((declared or {}).get("confirmed") or [])
    return [u for u in unresolved if f"{u['file']}:{u['line']}" not in confirmed]


def build_manifest(
    team: str,
    repo: str,
    commit: str,
    generated_at: str,
    tree: dict[str, str],
    default_component: str,
    declared: dict[str, Any] | None = None,
    environments: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """De uma árvore de arquivos a um manifesto, só com metadados. A mesma árvore gera sempre o mesmo manifesto."""
    result = ScanResult()
    tf = {p: t for p, t in tree.items() if p.endswith(".tf")}
    cs = {p: t for p, t in tree.items() if p.endswith(".cs") and not TEST_NAME.search(p)}
    settings = {p: t for p, t in tree.items() if re.search(r"(^|/)appsettings[\w.]*\.json$", p)}
    projects = [p for p in tree if p.endswith(".csproj") and not TEST_NAME.search(p)]
    deployable = sorted(
        p
        for p in projects
        if any(ENTRY.search(t) for q, t in cs.items() if q.startswith(p.rsplit("/", 1)[0] + "/" if "/" in p else ""))
    )

    def component_of(path: str) -> str:
        """Componente é o que é implantado: o projeto mais próximo do arquivo, ou o repositório."""
        best, name = -1, default_component
        for project in deployable or projects:
            folder = project.rsplit("/", 1)[0] + "/" if "/" in project else ""
            if path.startswith(folder) and len(folder) > best:
                best = len(folder)
                name = project.rsplit("/", 1)[-1].removesuffix(".csproj").lower().replace(".", "-")
        return name

    config = scan_config(settings)
    scan_terraform(tf, result)
    scan_csharp(cs, config, result, component_of)
    for path, text in sorted(tree.items()):
        if re.search(r"asyncapi.*\.ya?ml$", path, re.I) or re.search(r"(^|/)contracts/.+\.asyncapi\.ya?ml$", path):
            scan_contract(path, text, result, default_component)
    return _assemble(team, repo, commit, generated_at, result, default_component, declared, environments)


def _assemble(
    team: str,
    repo: str,
    commit: str,
    generated_at: str,
    result: ScanResult,
    default_component: str,
    declared: dict[str, Any] | None,
    environments: tuple[str, ...] | None,
) -> dict[str, Any]:
    from nexo.modules.lineage.domain.model import DEFAULT_ENV, SCHEMA

    env = environments or DEFAULT_ENV
    subscriptions: dict[str, list[Finding]] = {}
    components: dict[str, dict[str, Any]] = {}

    def comp(name: str) -> dict[str, Any]:
        cid = f"{team}/{name or default_component}"
        return components.setdefault(cid, {"id": cid, "runtime": result.components.get(name, "")})

    rank = {"high": 0, "medium": 1, "low": 2}
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for f in sorted(result.findings, key=lambda x: (rank.get(x.confidence, 3), x.file, x.line)):
        if f.direction == "subscribes":
            subscriptions.setdefault(f.component.removeprefix("sqs:"), []).append(f)
            continue
        channel, _relation = normalize(f.kind, f.name, env) if f.kind != "http" else (f"http:{f.name}", "")
        key = (f.component or default_component, f.direction, channel)
        if key in seen:
            if f.message and not seen[key].get("message"):
                seen[key]["message"] = f.message
            continue
        link: dict[str, Any] = {
            "channel": channel,
            "evidence": {"source": f.source, "file": f.file, "line": f.line, "confidence": f.confidence},
        }
        if f.message:
            link["message"] = f.message
        seen[key] = link
        comp(f.component).setdefault(f.direction, []).append(link)
    # SNS→SQS: quem consome a fila recebe também o tópico assinado
    for queue, subs in subscriptions.items():
        queue_channel, _ = normalize("sqs", queue, env)
        consumers = [k[0] for k in seen if k[1] == "consumes" and k[2] == queue_channel]
        for f in subs:
            channel, _ = normalize("sns", f.name, env)
            for consumer in consumers or [default_component]:
                key = (consumer, "consumes", channel)
                if key not in seen:
                    link = {
                        "channel": channel,
                        "evidence": {"source": f.source, "file": f.file, "line": f.line, "confidence": f.confidence},
                    }
                    seen[key] = link
                    comp(consumer).setdefault("consumes", []).append(link)
    for name, runtime in result.components.items():
        if name:
            comp(name)["runtime"] = runtime
    for block in (declared or {}).get("components") or []:
        name = str(block.get("name") or default_component)
        target = comp(name)
        if block.get("runtime"):
            target["runtime"] = block["runtime"]
        for direction in ("publishes", "consumes"):
            for item in block.get(direction) or []:
                channel, _ = normalize(str(item.get("kind", "kafka")), str(item["name"]), env)
                if (name, direction, channel) in seen:
                    continue
                link = {
                    "channel": channel,
                    "evidence": {
                        "source": "declared",
                        "file": ".nexo/component.yaml",
                        "line": 0,
                        "confidence": "declared",
                    },
                }
                if item.get("message"):
                    link["message"] = str(item["message"])
                target.setdefault(direction, []).append(link)
    ordered = []
    for cid in sorted(components):
        block = components[cid]
        for direction in ("publishes", "consumes", "exposes", "calls"):
            if direction in block:
                block[direction] = sorted(block[direction], key=lambda x: x["channel"])
        ordered.append(block)
    return {
        "schema": SCHEMA,
        "team": team,
        "repo": repo,
        "commit": commit,
        "generated_at": generated_at,
        "scanner": SCANNER_VERSION,
        "components": ordered,
        "unresolved": sorted(_open(result.unresolved, declared), key=lambda u: (u["file"], u["line"], u["reason"])),
    }
