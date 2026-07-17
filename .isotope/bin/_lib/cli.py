"""Argument parsing and command dispatch for Isotope."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import docs as isotope_docs
from . import acceptance, analyze, construction, decision, design, expression, feedback, gitops, intake, invocations, operations, owners, quanta, review, setup, specimens, valence
from .errors import EXIT_INTERNAL, EXIT_MALFORMED, EXIT_NOT_FOUND, EXIT_REFUSED, EXIT_USAGE, IsotopeError
from .journal import recover
from .operating import read_operating
from .output import envelope, error_envelope, render
from .paths import Project, resolve_project
from .revisions import parse_json, revision
from .schemas import get as get_schema
from .schemas import names as schema_names
from .schemas import validate as validate_schema


LOG_KINDS = (
    "analysis", "design-context", "acceptance-criterion", "change", "verification",
    "round", "assay", "acceptance", "decision", "outcome",
)
TRANSACTIONS = {
    "specimen.round.append": specimens.round_append,
    "specimen.assay.append": specimens.assay_append,
    "specimen.acceptance.append": specimens.acceptance_append,
    "specimen.decision.add": specimens.decision_add,
    "specimen.outcome.set": specimens.outcome_set,
}


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise IsotopeError("usage", message, EXIT_USAGE)


def build_parser() -> Parser:
    parser = Parser(prog="isotope", description="Deterministic Isotope repository operations.")
    parser.add_argument("--project", help="resolve the project from this directory")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    commands = parser.add_subparsers(dest="command", required=True)

    schema = commands.add_parser("schema", help="list or publish a versioned schema")
    schema.add_argument("entity", nargs="?", help="schema entity name")
    schema.set_defaults(operation="schema")

    specimen = commands.add_parser("specimen", help="read, author, and transact specimens")
    specimen_commands = specimen.add_subparsers(dest="specimen_command", required=True)
    for name in ("locate", "status", "show", "dependencies", "validate"):
        command = specimen_commands.add_parser(name, help=f"{name} a specimen")
        command.add_argument("slug")
        command.set_defaults(operation=f"specimen.{name}")

    change_read = specimen_commands.add_parser("change", help="return one planned change whole")
    change_read.add_argument("slug")
    change_read.add_argument("number", type=int)
    change_read.set_defaults(operation="specimen.change")

    decisions_read = specimen_commands.add_parser("decisions", help="list decisions with in-force state")
    decisions_read.add_argument("slug")
    decisions_read.set_defaults(operation="specimen.decisions")

    log = specimen_commands.add_parser("log", help="project the audited operation log from events")
    log.add_argument("slug")
    log.add_argument("--change", type=int)
    log.add_argument("--round", type=int)
    log.add_argument("--kind", choices=LOG_KINDS)
    log.add_argument("--status")
    log.add_argument("--view", choices=("summary", "full"), default="summary")
    log.add_argument("--limit", type=int, default=20)
    log.add_argument("--cursor")
    log.set_defaults(operation="specimen.log")

    outcome_read = specimen_commands.add_parser("outcome", help="return the deploy expression packet")
    outcome_read.add_argument("slug")
    outcome_read.set_defaults(operation="specimen.outcome")

    create = specimen_commands.add_parser("create", help="birth a matter specimen from intake data")
    create.add_argument("slug")
    create.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    create.set_defaults(operation="specimen.create")

    set_command = specimen_commands.add_parser("set", help="set reaction-owned fields on a matter specimen")
    set_command.add_argument("slug")
    set_command.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    set_command.set_defaults(operation="specimen.set")

    promote = specimen_commands.add_parser("promote", help="journaled culture move matter -> flux")
    promote.add_argument("slug")
    promote.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    promote.set_defaults(operation="specimen.promote")
    demote = specimen_commands.add_parser("demote", help="journaled culture move flux -> matter for core rework")
    demote.add_argument("slug")
    demote.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    demote.set_defaults(operation="specimen.demote")
    stabilize = specimen_commands.add_parser("stabilize", help="journaled deploy-ready culture move flux -> stable")
    stabilize.add_argument("slug")
    stabilize.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    stabilize.set_defaults(operation="specimen.stabilize")

    appends = (
        ("round", ("append",)),
        ("assay", ("append",)),
        ("acceptance", ("append",)),
        ("decision", ("add", "supersede")),
    )
    for noun, verbs in appends:
        noun_parser = specimen_commands.add_parser(noun, help=f"audited {noun} transactions")
        verb_commands = noun_parser.add_subparsers(dest="verb", required=True)
        for verb in verbs:
            command = verb_commands.add_parser(verb)
            command.add_argument("slug")
            if (noun, verb) == ("decision", "supersede"):
                command.add_argument("decision_id")
            command.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
            command.set_defaults(operation=f"specimen.{noun}.{verb}")

    revise = specimen_commands.add_parser("change-revise", help="audited change replacement (invoked as: specimen change revise)")
    revise.add_argument("slug")
    revise.add_argument("number", type=int)
    revise.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    revise.set_defaults(operation="specimen.change.revise")

    outcome_set = specimen_commands.add_parser("outcome-set", help="audited outcome record (invoked as: specimen outcome set)")
    outcome_set.add_argument("slug")
    outcome_set.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    outcome_set.set_defaults(operation="specimen.outcome.set")

    run = commands.add_parser("run", help="operating lifecycle state machines")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    run_status = run_commands.add_parser("status", help="armed-state diagnosis")
    run_status.set_defaults(operation="run.status")
    run_arm = run_commands.add_parser("arm", help="arm one flux specimen")
    run_arm.add_argument("slug")
    run_arm.add_argument("--branch", required=True)
    run_arm.add_argument("--base", required=True)
    run_arm.set_defaults(operation="run.arm")
    run_teardown = run_commands.add_parser("teardown", help="tear down the armed operation")
    run_teardown.add_argument("slug")
    run_teardown.set_defaults(operation="run.teardown")
    run_park = run_commands.add_parser("park", help="pause the armed operation on its bound branch")
    run_park.add_argument("slug")
    run_park.set_defaults(operation="run.park")
    run_resume = run_commands.add_parser("resume", help="resume one unchanged parked operation")
    run_resume.add_argument("slug")
    run_resume.set_defaults(operation="run.resume")
    run_deploy = run_commands.add_parser("deploy", help="stabilize, squash-land, and clean up one accepted operation")
    run_deploy.add_argument("slug")
    run_deploy.add_argument("--input", required=True, help="deploy JSON file, or - for stdin")
    run_deploy.set_defaults(operation="run.deploy")
    run_cleanup = run_commands.add_parser("cleanup", help="finish a recorded landed operation")
    run_cleanup.add_argument("slug")
    run_cleanup.set_defaults(operation="run.cleanup")

    git = commands.add_parser("git", help="bounded semantic Git operations")
    git_commands = git.add_subparsers(dest="git_command", required=True)
    snapshot = git_commands.add_parser("review-snapshot", help="canonical Review snapshot views")
    snapshot.add_argument("slug")
    snapshot.add_argument("--change", type=int, required=True)
    snapshot.add_argument("--round", type=int, required=True)
    snapshot.add_argument("--view", choices=("metadata", "patch"), default="metadata")
    snapshot.set_defaults(operation="git.review-snapshot")
    git_status = git_commands.add_parser("status", help="narrow semantic Git status")
    git_status.set_defaults(operation="git.status")
    git_commit = git_commands.add_parser("commit", help="commit exactly declared files for an armed specimen")
    git_commit.add_argument("slug")
    git_commit.add_argument("--input", required=True, help="semantic commit JSON file, or - for stdin")
    git_commit.set_defaults(operation="git.commit")
    git_cleanup = git_commands.add_parser("cleanup", help="unstage the current index without changing worktree bytes")
    git_cleanup.add_argument("slug")
    git_cleanup.set_defaults(operation="git.cleanup")

    setup_parser = commands.add_parser("setup", help="synchronize and observe composite assets")
    setup_commands = setup_parser.add_subparsers(dest="setup_command", required=True)
    setup_init = setup_commands.add_parser("init", help="create the validated consumer manifest")
    setup_init.add_argument("--input", required=True, help="manifest JSON file, or - for stdin")
    setup_init.set_defaults(operation="setup.init")
    setup_inspect = setup_commands.add_parser("inspect", help="pure generated-asset drift diagnosis")
    setup_inspect.set_defaults(operation="setup.inspect")
    setup_sync = setup_commands.add_parser("sync", help="journaled shared CLI and native asset synchronization")
    setup_sync.set_defaults(operation="setup.sync")
    setup_observe = setup_commands.add_parser("observe", help="record one capability-scoped host observation")
    setup_observe.add_argument("--host", choices=("claude", "codex"), required=True)
    setup_observe.add_argument("--source-version", required=True)
    setup_observe.add_argument("--adapter-version", required=True)
    setup_observe.set_defaults(operation="setup.observe")

    registry = commands.add_parser("registry", help="inspect consumer host/model availability")
    registry_commands = registry.add_subparsers(dest="registry_command", required=True)
    registry_show = registry_commands.add_parser("show", help="show the validated consumer registry")
    registry_show.set_defaults(operation="registry.show")
    registry_validate = registry_commands.add_parser("validate", help="validate the consumer registry")
    registry_validate.set_defaults(operation="registry.validate")
    registry_host = registry_commands.add_parser("host", help="semantically enable or disable one host")
    registry_host_commands = registry_host.add_subparsers(dest="registry_host_command", required=True)
    for verb in ("enable", "disable"):
        command = registry_host_commands.add_parser(verb)
        command.add_argument("host", choices=setup.SUPPORTED_HOSTS)
        command.set_defaults(operation=f"registry.host.{verb}")
    registry_model = registry_commands.add_parser("model", help="semantically add or remove one model")
    registry_model_commands = registry_model.add_subparsers(dest="registry_model_command", required=True)
    registry_model_add = registry_model_commands.add_parser("add")
    registry_model_add.add_argument("host", choices=setup.SUPPORTED_HOSTS)
    registry_model_add.add_argument("--input", required=True, help="registry model JSON file, or - for stdin")
    registry_model_add.set_defaults(operation="registry.model.add")
    registry_model_remove = registry_model_commands.add_parser("remove")
    registry_model_remove.add_argument("host", choices=setup.SUPPORTED_HOSTS)
    registry_model_remove.add_argument("model")
    registry_model_remove.set_defaults(operation="registry.model.remove")

    architect = commands.add_parser("architect", help="inspect durable consumer-repository shape")
    architect_commands = architect.add_subparsers(dest="architect_command", required=True)
    architect_inspect = architect_commands.add_parser("inspect", help="narrow Atlas, gate, registry, reaction, and synthesis health")
    architect_inspect.set_defaults(operation="architect.inspect")

    operate = commands.add_parser("operate", help="coordinate lifecycle continuity from narrow owner state")
    operate_commands = operate.add_subparsers(dest="operate_command", required=True)
    operate_status = operate_commands.add_parser("status", help="bounded goals, readiness, questions, and completion highlights")
    operate_status.add_argument("--limit", type=int, default=20)
    operate_status.set_defaults(operation="operate.status")

    agent = commands.add_parser("agent", help="catalyst invocation records")
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    agent_options = agent_commands.add_parser("options", help="list enabled registry choices")
    agent_options.add_argument("reaction", nargs="?")
    agent_options.set_defaults(operation="agent.options")
    agent_inspect = agent_commands.add_parser("inspect", help="resolve reaction readiness without selected values")
    agent_inspect.add_argument("reaction")
    agent_inspect.add_argument("slug", nargs="?")
    agent_inspect.add_argument("--host", choices=("claude", "codex"))
    agent_inspect.add_argument("--model")
    agent_inspect.add_argument("--change", type=int)
    agent_inspect.add_argument("--round", dest="round_number", type=int)
    agent_inspect.add_argument("--after")
    agent_inspect.add_argument("--mode", choices=("add", "supersede", "capture", "rework"))
    agent_inspect.add_argument("--decision")
    agent_inspect.add_argument("--question-invocation")
    agent_inspect.add_argument("--question")
    agent_inspect.add_argument("--acceptance", type=int)
    agent_inspect.add_argument("--dump")
    agent_inspect.set_defaults(operation="agent.inspect")
    agent_open = agent_commands.add_parser("open", help="freeze one native reaction invocation")
    agent_open.add_argument("reaction")
    agent_open.add_argument("slug", nargs="?")
    agent_open.add_argument("--model")
    agent_open.add_argument("--change", type=int)
    agent_open.add_argument("--round", dest="round_number", type=int)
    agent_open.add_argument("--after")
    agent_open.add_argument("--mode", choices=("add", "supersede", "capture", "rework"))
    agent_open.add_argument("--decision")
    agent_open.add_argument("--question-invocation")
    agent_open.add_argument("--question")
    agent_open.add_argument("--acceptance", type=int)
    agent_open.add_argument("--dump")
    agent_open.set_defaults(operation="agent.open")
    agent_brief = agent_commands.add_parser("brief", help="pure one-command catalyst projection")
    agent_brief.add_argument("reaction")
    agent_brief.add_argument("--invocation", required=True)
    agent_brief.set_defaults(operation="agent.brief")
    agent_finish = agent_commands.add_parser("finish", help="validate and broker one read-only Review readout")
    agent_finish.add_argument("reaction")
    agent_finish.add_argument("--invocation", required=True)
    agent_finish.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    agent_finish.set_defaults(operation="agent.finish")
    agent_record = agent_commands.add_parser("record", help="record one native write-capable reaction result")
    agent_record.add_argument("reaction")
    agent_record.add_argument("--invocation", required=True)
    agent_record.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    agent_record.set_defaults(operation="agent.record")
    agent_status = agent_commands.add_parser("status", help="compact invocation status")
    agent_status.add_argument("invocation_id")
    agent_status.set_defaults(operation="agent.status")
    agent_answer = agent_commands.add_parser("answer", help="record one semantic answer")
    agent_answer.add_argument("invocation_id")
    agent_answer.add_argument("question_id")
    agent_answer.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    agent_answer.set_defaults(operation="agent.answer")
    agent_map = agent_commands.add_parser("map", help="generate protocol context maps")
    agent_map.add_argument("reaction", nargs="?")
    agent_map.add_argument("--map-format", choices=("json", "mermaid"), default="json")
    agent_map.set_defaults(operation="agent.map")
    agent_invoke = agent_commands.add_parser("invoke", help="launch one read-only brokered Review")
    agent_invoke.add_argument("reaction")
    agent_invoke.add_argument("slug", nargs="?")
    agent_invoke.add_argument("--host", choices=("claude", "codex"), required=True)
    agent_invoke.add_argument("--model")
    agent_invoke.add_argument("--change", type=int)
    agent_invoke.add_argument("--round", dest="round_number", type=int)
    agent_invoke.add_argument("--after")
    agent_invoke.add_argument("--mode", choices=("add", "supersede", "capture", "rework"))
    agent_invoke.add_argument("--decision")
    agent_invoke.add_argument("--question-invocation")
    agent_invoke.add_argument("--question")
    agent_invoke.add_argument("--acceptance", type=int)
    agent_invoke.add_argument("--dump")
    agent_invoke.add_argument("--timeout", type=float, default=600.0)
    agent_invoke.set_defaults(operation="agent.invoke")

    quanta_parser = commands.add_parser("quanta", help="record and query discrete cited evidence")
    quanta_commands = quanta_parser.add_subparsers(dest="quanta_command", required=True)
    quanta_record = quanta_commands.add_parser("record", help="record one cited evidence quantum")
    quanta_record.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    quanta_record.set_defaults(operation="quanta.record")
    quanta_list = quanta_commands.add_parser("list", help="query quanta with selectors and pagination")
    quanta_list.add_argument("--type", dest="quantum_type", choices=quanta.QUANTUM_TYPES)
    quanta_list.add_argument("--slug")
    quanta_list.add_argument("--invocation")
    quanta_list.add_argument("--signature")
    quanta_list.add_argument("--limit", type=int, default=20)
    quanta_list.add_argument("--cursor")
    quanta_list.set_defaults(operation="quanta.list")
    quanta_show = quanta_commands.add_parser("show", help="return one quantum whole")
    quanta_show.add_argument("quantum_id")
    quanta_show.set_defaults(operation="quanta.show")

    tool = commands.add_parser("tool", help="repo-local deterministic Valence tools")
    tool_commands = tool.add_subparsers(dest="tool_command", required=True)
    tool_scan = tool_commands.add_parser("scan", help="discover and diagnose every tool descriptor")
    tool_scan.set_defaults(operation="tool.scan")
    tool_list = tool_commands.add_parser("list", help="summarize the valid tools")
    tool_list.set_defaults(operation="tool.list")
    tool_inspect = tool_commands.add_parser("inspect", help="return one descriptor with citation resolution")
    tool_inspect.add_argument("name")
    tool_inspect.set_defaults(operation="tool.inspect")
    tool_run = tool_commands.add_parser("run", help="execute one tool within repository authority")
    tool_run.add_argument("name")
    tool_run.add_argument("--set", dest="assignments", action="append", default=[], metavar="NAME=VALUE")
    tool_run.add_argument("--timeout", type=float, default=300.0)
    tool_run.set_defaults(operation="tool.run")
    tool_suggest = tool_commands.add_parser("suggest", help="aggregate repeated command evidence into tool suggestions")
    tool_suggest.set_defaults(operation="tool.suggest")
    tool_scaffold = tool_commands.add_parser("scaffold", help="write one cited reviewable tool descriptor")
    tool_scaffold.add_argument("name")
    tool_scaffold.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    tool_scaffold.set_defaults(operation="tool.scaffold")
    tool_validate = tool_commands.add_parser("validate", help="run one tool's declared validation")
    tool_validate.add_argument("name")
    tool_validate.add_argument("--timeout", type=float, default=300.0)
    tool_validate.set_defaults(operation="tool.validate")

    feedback_parser = commands.add_parser("feedback", help="portable consumer-to-toolkit feedback bundles")
    feedback_commands = feedback_parser.add_subparsers(dest="feedback_command", required=True)
    feedback_export = feedback_commands.add_parser("export", help="export one matter specimen with cited evidence")
    feedback_export.add_argument("slug")
    feedback_export.add_argument("--input", required=True, help="JSON payload file, or - for stdin")
    feedback_export.add_argument("--output", required=True, help="destination bundle path")
    feedback_export.set_defaults(operation="feedback.export")
    feedback_validate = feedback_commands.add_parser("validate", help="validate one bundle against its schema alone")
    feedback_validate.add_argument("--input", required=True, help="bundle JSON file, or - for stdin")
    feedback_validate.set_defaults(operation="feedback.validate")

    docs = commands.add_parser("docs", help="retrieve and validate human documentation")
    docs_commands = docs.add_subparsers(dest="docs_command", required=True)
    docs_map = docs_commands.add_parser("map", help="list the manifest documentation index")
    docs_map.set_defaults(operation="docs.map")
    docs_section = docs_commands.add_parser("section", help="return one marked region whole")
    docs_section.add_argument("path")
    docs_section.add_argument("section_id")
    docs_section.set_defaults(operation="docs.section")
    docs_validate = docs_commands.add_parser("validate", help="validate mapped files and markers")
    docs_validate.set_defaults(operation="docs.validate")
    return parser


def _read_input(spec: str):
    if spec == "-":
        return parse_json(sys.stdin.read(), "<stdin>")
    try:
        text = Path(spec).read_text(encoding="utf-8")
    except OSError as exc:
        raise IsotopeError(
            "input-not-found",
            "The input file could not be read.",
            EXIT_NOT_FOUND,
            {"path": spec, "reason": str(exc)},
        ) from exc
    except UnicodeError as exc:
        raise IsotopeError(
            "malformed-json",
            "The input file is not readable UTF-8.",
            EXIT_MALFORMED,
            {"path": spec, "reason": str(exc)},
        ) from exc
    return parse_json(text, spec)


def _dispatch_specimen(args: argparse.Namespace, project: Project) -> dict:
    operation = args.operation
    if operation in ("specimen.create", "specimen.set", "specimen.promote", "specimen.demote", "specimen.stabilize"):
        author = {
            "specimen.create": specimens.create,
            "specimen.set": specimens.set_fields,
            "specimen.promote": specimens.promote,
            "specimen.demote": specimens.demote,
            "specimen.stabilize": specimens.stabilize,
        }[operation]
        located, specimen_revision = author(project, args.slug, _read_input(args.input))
        data = {"slug": args.slug, "stage": located.stage, "revision": specimen_revision}
        return envelope(operation, "ok", project=project, source=located.source(specimen_revision), data=data)
    if operation in TRANSACTIONS or operation in ("specimen.decision.supersede", "specimen.change.revise"):
        payload = _read_input(args.input)
        if operation == "specimen.decision.supersede":
            located, specimen_revision, event = specimens.decision_supersede(project, args.slug, args.decision_id, payload)
        elif operation == "specimen.change.revise":
            located, specimen_revision, event = specimens.change_revise(project, args.slug, args.number, payload)
        else:
            located, specimen_revision, event = TRANSACTIONS[operation](project, args.slug, payload)
        data = {"slug": args.slug, "revision": specimen_revision, "event": event}
        return envelope(operation, "ok", project=project, source=located.source(specimen_revision), data=data)

    located = specimens.locate(project, args.slug)
    if operation == "specimen.locate":
        return envelope(operation, "ok", project=project, source=located.source(), data=specimens.locate_data(located))
    value, specimen_revision = specimens.read_validated(located)
    source = located.source(specimen_revision)
    if operation == "specimen.status":
        data = {"slug": args.slug, "stage": located.stage, "valid": True, "revision": specimen_revision}
    elif operation == "specimen.show":
        data = {"slug": args.slug, "stage": located.stage, "specimen": value, "text": specimens.text_projection(located, value)}
    elif operation == "specimen.dependencies":
        dependencies = []
        for slug in value["depends_on"]:
            try:
                dependency = specimens.locate(project, slug)
                dependencies.append({"slug": slug, "status": "found", "stage": dependency.stage, "path": dependency.relative_path})
            except IsotopeError as exc:
                if exc.code != "specimen-not-found":
                    raise
                dependencies.append({"slug": slug, "status": "missing", "stage": None, "path": None})
        data = {"slug": args.slug, "depends_on": dependencies}
    elif operation == "specimen.validate":
        data = {"slug": args.slug, "stage": located.stage, "valid": True, "canonical": True, "revision": specimen_revision}
    elif operation == "specimen.change":
        data = {"slug": args.slug, "change": specimens.change_record(value, args.number)}
    elif operation == "specimen.decisions":
        data = {"slug": args.slug, "decisions": specimens.decision_records(value)}
    elif operation == "specimen.outcome":
        data = specimens.outcome_packet(value)
    elif operation == "specimen.log":
        if args.limit < 1:
            raise IsotopeError("usage", "--limit must be at least 1.", EXIT_USAGE)
        records = specimens.filter_log(
            specimens.log_records(value, view=args.view),
            change=args.change,
            round_number=args.round,
            kind=args.kind,
            status=args.status,
        )
        window, page = specimens.paginate(records, source_revision=specimen_revision, limit=args.limit, cursor=args.cursor)
        return envelope(operation, "ok", project=project, source=source, page=page, data={"slug": args.slug, "records": window, "total": len(records)})
    else:  # pragma: no cover
        raise IsotopeError("usage", "Unknown specimen command.", EXIT_USAGE)
    return envelope(operation, "ok", project=project, source=source, data=data)


def _dispatch_run(args: argparse.Namespace, project: Project) -> dict:
    if args.operation == "run.status":
        return envelope(args.operation, "ok", project=project, data=operations.status(project))
    if args.operation == "run.arm":
        operating = operations.arm(project, args.slug, args.branch, args.base)
        return envelope(args.operation, "ok", project=project, data={"armed": True, "operating": operating})
    if args.operation == "run.teardown":
        return envelope(args.operation, "ok", project=project, data=operations.teardown(project, args.slug))
    if args.operation == "run.park":
        return envelope(args.operation, "ok", project=project, data={"operating": operations.park(project, args.slug)})
    if args.operation == "run.resume":
        return envelope(args.operation, "ok", project=project, data={"operating": operations.resume(project, args.slug)})
    if args.operation == "run.deploy":
        return envelope(args.operation, "ok", project=project, data=operations.deploy(project, args.slug, _read_input(args.input)))
    if args.operation == "run.cleanup":
        return envelope(args.operation, "ok", project=project, data=operations.cleanup(project, args.slug))
    raise IsotopeError("usage", "Unknown run command.", EXIT_USAGE)  # pragma: no cover


def _dispatch_git(args: argparse.Namespace, project: Project) -> dict:
    operating = read_operating(project)
    if args.operation == "git.status":
        return envelope(args.operation, "ok", project=project, data=gitops.semantic_status(project, operating))
    if args.operation in ("git.commit", "git.cleanup"):
        if operating is None or operating["slug"] != args.slug or operating["state"] != "armed":
            raise IsotopeError("no-armed-operation", "Semantic Git mutation requires the matching armed operation.", EXIT_REFUSED, {"slug": args.slug})
        if gitops.current_branch(project) != operating["branch"]:
            raise IsotopeError("branch-mismatch", "Semantic Git mutation requires the operation branch.", EXIT_REFUSED, {"branch": operating["branch"]})
        if args.operation == "git.cleanup":
            data = gitops.cleanup_index(project)
        else:
            payload = _read_input(args.input)
            validate_schema("semantic-commit-input", payload)
            data = gitops.semantic_commit(
                project,
                expected_head=payload["expected_head"],
                files=payload["files"],
                reason=payload["reason"],
            )
        return envelope(args.operation, "ok", project=project, data={"slug": args.slug, **data})
    located = specimens.locate(project, args.slug)
    value, specimen_revision = specimens.read_validated(located)
    if operating is None or operating["slug"] != args.slug:
        raise IsotopeError(
            "no-armed-operation",
            "The Review snapshot binds an armed operation's base commit.",
            EXIT_REFUSED,
            {"slug": args.slug, "armed": None if operating is None else operating["slug"]},
        )
    target = next(
        (item for item in value["rounds"] if item["change"] == args.change and item["number"] == args.round),
        None,
    )
    if target is None:
        raise IsotopeError(
            "round-not-found",
            "No recorded round matches the requested change and round.",
            EXIT_NOT_FOUND,
            {"change": args.change, "round": args.round},
        )
    manifest, live_revision = gitops.review_snapshot(project, operating["base_commit"])
    stored = target.get("review_snapshot")
    stored_revision = None if stored is None else revision(stored)
    data = {
        "slug": args.slug,
        "change": args.change,
        "round": args.round,
        "view": args.view,
        "revision": live_revision,
        "stored_revision": stored_revision,
        "matches_stored": stored_revision == live_revision,
    }
    if args.view == "metadata":
        data["snapshot"] = manifest
    else:
        data["patch"] = gitops.snapshot_patch(project, operating["base_commit"])
    return envelope(args.operation, "ok", project=project, source=located.source(specimen_revision), data=data)


def _dispatch_agent(args: argparse.Namespace, project: Project) -> dict:
    if args.operation == "agent.options":
        if args.reaction == "acceptance":
            return envelope(args.operation, "ok", project=project, data=acceptance.options(project))
        if args.reaction == "analyze":
            return envelope(args.operation, "ok", project=project, data=analyze.options(project))
        if args.reaction == "construction":
            return envelope(args.operation, "ok", project=project, data=construction.options(project))
        if args.reaction == "decision":
            return envelope(args.operation, "ok", project=project, data=decision.options(project))
        if args.reaction == "design":
            return envelope(args.operation, "ok", project=project, data=design.options(project))
        if args.reaction == "expression":
            return envelope(args.operation, "ok", project=project, data=expression.options(project))
        if args.reaction == "intake":
            return envelope(args.operation, "ok", project=project, data=intake.options(project))
        return envelope(args.operation, "ok", project=project, data=review.options(project, args.reaction))
    if args.operation == "agent.inspect":
        if args.reaction == "acceptance":
            data = acceptance.inspect(project, args.slug, host=args.host, model=args.model, acceptance_number=args.acceptance, after=args.after)
        elif args.reaction == "analyze":
            data = analyze.inspect(project, args.slug, host=args.host, model=args.model, after=args.after)
        elif args.reaction == "construction":
            data = construction.inspect(project, args.slug, host=args.host, model=args.model, change=args.change, round_number=args.round_number, after=args.after)
        elif args.reaction == "decision":
            data = decision.inspect(project, args.slug, host=args.host, model=args.model, mode=args.mode, decision_id=args.decision, question_invocation=args.question_invocation, question_id=args.question, after=args.after)
        elif args.reaction == "design":
            data = design.inspect(project, args.slug, host=args.host, model=args.model, after=args.after)
        elif args.reaction == "expression":
            data = expression.inspect(project, args.slug, host=args.host, model=args.model, after=args.after)
        elif args.reaction == "intake":
            data = intake.inspect(project, args.slug, host=args.host, model=args.model, mode=args.mode, dump=args.dump, after=args.after)
        else:
            data = review.inspect(project, args.reaction, args.slug, host=args.host, model=args.model, change=args.change, round_number=args.round_number, after=args.after)
        return envelope(args.operation, "ok", project=project, data=data)
    if args.operation == "agent.open":
        host = os.environ.get("ISOTOPE_HOST")
        if host not in ("claude", "codex"):
            raise IsotopeError("authority-unavailable", "The controlling native wrapper must set ISOTOPE_HOST.", EXIT_REFUSED, {"next_action": "run through the active Claude or Codex adapter"})
        if args.reaction == "acceptance":
            data = acceptance.open_invocation(project, args.slug, host=host, model=args.model, acceptance_number=args.acceptance, after=args.after)
        elif args.reaction == "analyze":
            data = analyze.open_invocation(project, args.slug, host=host, model=args.model, after=args.after)
        elif args.reaction == "construction":
            data = construction.open_invocation(project, args.slug, host=host, model=args.model, change=args.change, round_number=args.round_number, after=args.after)
        elif args.reaction == "decision":
            data = decision.open_invocation(project, args.slug, host=host, model=args.model, mode=args.mode, decision_id=args.decision, question_invocation=args.question_invocation, question_id=args.question, after=args.after)
        elif args.reaction == "design":
            data = design.open_invocation(project, args.slug, host=host, model=args.model, after=args.after)
        elif args.reaction == "expression":
            data = expression.open_invocation(project, args.slug, host=host, model=args.model, after=args.after)
        elif args.reaction == "intake":
            data = intake.open_invocation(project, args.slug, host=host, model=args.model, mode=args.mode, dump=args.dump, after=args.after)
        else:
            data = review.open_invocation(project, args.reaction, args.slug, host=host, model=args.model, change=args.change, round_number=args.round_number, after=args.after)
        return envelope(args.operation, "ok", project=project, data=data)
    if args.operation == "agent.brief":
        if args.reaction == "acceptance":
            data = acceptance.brief(project, args.invocation)
        elif args.reaction == "analyze":
            data = analyze.brief(project, args.invocation)
        elif args.reaction == "construction":
            data = construction.brief(project, args.invocation)
        elif args.reaction == "decision":
            data = decision.brief(project, args.invocation)
        elif args.reaction == "design":
            data = design.brief(project, args.invocation)
        elif args.reaction == "expression":
            data = expression.brief(project, args.invocation)
        elif args.reaction == "intake":
            data = intake.brief(project, args.invocation)
        else:
            data = review.brief(project, args.reaction, args.invocation)
        return envelope(args.operation, "ok", project=project, data=data)
    if args.operation == "agent.finish":
        if args.reaction in ("acceptance", "analyze", "construction", "decision", "design", "expression", "intake"):
            raise IsotopeError("usage", f"{args.reaction.title()} results use agent record.", EXIT_USAGE)
        data = review.finish(project, args.reaction, args.invocation, _read_input(args.input), os.environ.get("ISOTOPE_COMPLETION_CAPABILITY"))
        return envelope(args.operation, "ok", project=project, data=data)
    if args.operation == "agent.record":
        if args.reaction == "acceptance":
            data = acceptance.record(project, args.invocation, _read_input(args.input))
        elif args.reaction == "analyze":
            data = analyze.record(project, args.invocation, _read_input(args.input))
        elif args.reaction == "construction":
            data = construction.record(project, args.invocation, _read_input(args.input))
        elif args.reaction == "decision":
            data = decision.record(project, args.invocation, _read_input(args.input))
        elif args.reaction == "design":
            data = design.record(project, args.invocation, _read_input(args.input))
        elif args.reaction == "expression":
            data = expression.record(project, args.invocation, _read_input(args.input))
        elif args.reaction == "intake":
            data = intake.record(project, args.invocation, _read_input(args.input))
        else:
            raise IsotopeError("usage", "The named reaction does not expose native agent record.", EXIT_USAGE)
        return envelope(args.operation, "ok", project=project, data=data)
    if args.operation == "agent.status":
        return envelope(args.operation, "ok", project=project, data=invocations.status_data(project, args.invocation_id))
    if args.operation == "agent.answer":
        payload = _read_input(args.input)
        invocations.answer(project, args.invocation_id, args.question_id, payload)
        data = invocations.status_data(project, args.invocation_id)
        data["answered"] = args.question_id
        return envelope(args.operation, "ok", project=project, data=data)
    if args.operation == "agent.map":
        if args.reaction == "acceptance":
            return envelope(args.operation, "ok", project=project, data=acceptance.map_data(args.map_format))
        if args.reaction == "analyze":
            return envelope(args.operation, "ok", project=project, data=analyze.map_data(args.map_format))
        if args.reaction == "construction":
            return envelope(args.operation, "ok", project=project, data=construction.map_data(args.map_format))
        if args.reaction == "decision":
            return envelope(args.operation, "ok", project=project, data=decision.map_data(args.map_format))
        if args.reaction == "design":
            return envelope(args.operation, "ok", project=project, data=design.map_data(args.map_format))
        if args.reaction == "expression":
            return envelope(args.operation, "ok", project=project, data=expression.map_data(args.map_format))
        if args.reaction == "intake":
            return envelope(args.operation, "ok", project=project, data=intake.map_data(args.map_format))
        return envelope(args.operation, "ok", project=project, data=review.map_data(args.reaction, args.map_format))
    if args.operation == "agent.invoke":
        if args.timeout <= 0:
            raise IsotopeError("usage", "--timeout must be positive.", EXIT_USAGE)
        if args.reaction in ("acceptance", "analyze", "construction", "decision", "design", "expression", "intake"):
            raise IsotopeError("authority-unavailable", f"{args.reaction.title()} is native-only because it requires declared semantic write authority.", EXIT_REFUSED, {"next_action": f"open the {args.reaction.title()} catalyst in the active host"})
        data = review.invoke(project, args.reaction, args.slug, host=args.host, model=args.model, change=args.change, round_number=args.round_number, after=args.after, timeout=args.timeout)
        return envelope(args.operation, "ok", project=project, data=data)
    raise IsotopeError("usage", "Unknown agent command.", EXIT_USAGE)  # pragma: no cover


def _dispatch_quanta(args: argparse.Namespace, project: Project) -> dict:
    if args.operation == "quanta.record":
        record, created = quanta.record(project, _read_input(args.input))
        source = {"path": quanta.quantum_relative(record["id"]), "revision": revision(record)}
        return envelope(args.operation, "ok", project=project, source=source, data={"quantum": record, "created": created})
    if args.operation == "quanta.show":
        record = quanta.read_quantum(project, args.quantum_id)
        source = {"path": quanta.quantum_relative(record["id"]), "revision": revision(record)}
        return envelope(args.operation, "ok", project=project, source=source, data={"quantum": record})
    if args.operation == "quanta.list":
        if args.limit < 1 or args.limit > 100:
            raise IsotopeError("usage", "--limit must be between 1 and 100.", EXIT_USAGE)
        records = quanta.read_all(project)
        listing = quanta.listing_revision(records)
        filtered = quanta.filter_records(
            records,
            quantum_type=args.quantum_type,
            slug=args.slug,
            invocation=args.invocation,
            signature=args.signature,
        )
        window, page = specimens.paginate(filtered, source_revision=listing, limit=args.limit, cursor=args.cursor)
        source = {"path": ".isotope/quanta", "revision": listing}
        return envelope(args.operation, "ok", project=project, source=source, page=page, data={"records": window, "total": len(filtered)})
    raise IsotopeError("usage", "Unknown quanta command.", EXIT_USAGE)  # pragma: no cover


def _parse_assignments(pairs: list[str]) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name:
            raise IsotopeError("usage", "--set takes NAME=VALUE pairs.", EXIT_USAGE, {"pair": pair})
        assignments[name] = value
    return assignments


def _dispatch_tool(args: argparse.Namespace, project: Project) -> dict:
    if args.operation == "tool.scan":
        return envelope(args.operation, "ok", project=project, data={"tools": valence.scan(project)})
    if args.operation == "tool.list":
        return envelope(args.operation, "ok", project=project, data={"tools": valence.list_tools(project)})
    if args.operation == "tool.inspect":
        data = valence.inspect(project, args.name)
        source = {"path": valence.tool_relative(args.name), "revision": data["revision"]}
        return envelope(args.operation, "ok", project=project, source=source, data=data)
    if args.operation == "tool.run":
        data = valence.run_tool(project, args.name, _parse_assignments(args.assignments), args.timeout)
        return envelope(args.operation, "ok", project=project, data=data)
    if args.operation == "tool.suggest":
        return envelope(args.operation, "ok", project=project, data=valence.suggest(project))
    if args.operation == "tool.scaffold":
        data = valence.scaffold(project, args.name, _read_input(args.input))
        source = {"path": data["path"], "revision": data["revision"]}
        return envelope(args.operation, "ok", project=project, source=source, data=data)
    if args.operation == "tool.validate":
        data = valence.validate_tool(project, args.name, args.timeout)
        return envelope(args.operation, "ok", project=project, data=data)
    raise IsotopeError("usage", "Unknown tool command.", EXIT_USAGE)  # pragma: no cover


def _dispatch(args: argparse.Namespace, project: Project) -> dict:
    if args.command == "schema":
        data = {"schemas": schema_names()} if args.entity is None else {
            "entity": args.entity,
            "schema": get_schema(args.entity),
        }
        return envelope("schema.list" if args.entity is None else "schema.show", "ok", project=project, data=data)
    if args.command in ("specimen", "run", "git", "agent", "setup", "registry", "architect", "operate", "quanta", "tool", "feedback"):
        recover(project)
        if args.command == "specimen":
            return _dispatch_specimen(args, project)
        if args.command == "run":
            return _dispatch_run(args, project)
        if args.command == "git":
            return _dispatch_git(args, project)
        if args.command == "setup":
            if args.operation == "setup.init":
                data = setup.initialize(project, _read_input(args.input))
            elif args.operation == "setup.inspect":
                data = setup.inspect(project)
            elif args.operation == "setup.sync":
                data = setup.sync(project)
            else:
                data = setup.observe(project, args.host, args.source_version, args.adapter_version)
            return envelope(args.operation, "ok", project=project, data=data)
        if args.command == "registry":
            if args.operation in ("registry.show", "registry.validate"):
                registry, registry_revision = setup.load_registry(project)
                data = {"valid": True, "revision": registry_revision}
                if args.operation == "registry.show":
                    data["registry"] = registry
            elif args.operation in ("registry.host.enable", "registry.host.disable"):
                data = setup.set_registry_host(project, args.host, enabled=args.operation.endswith("enable"))
                registry_revision = data["revision"]
            elif args.operation == "registry.model.add":
                data = setup.add_registry_model(project, args.host, _read_input(args.input))
                registry_revision = data["revision"]
            else:
                data = setup.remove_registry_model(project, args.host, args.model)
                registry_revision = data["revision"]
            return envelope(args.operation, "ok", project=project, source={"path": setup.REGISTRY_RELATIVE, "revision": registry_revision}, data=data)
        if args.command == "architect":
            return envelope(args.operation, "ok", project=project, data=owners.architect_inspect(project))
        if args.command == "operate":
            if args.limit < 1 or args.limit > 50:
                raise IsotopeError("usage", "--limit must be between 1 and 50.", EXIT_USAGE)
            return envelope(args.operation, "ok", project=project, data=owners.operate_status(project, limit=args.limit))
        if args.command == "quanta":
            return _dispatch_quanta(args, project)
        if args.command == "tool":
            return _dispatch_tool(args, project)
        if args.command == "feedback":
            if args.operation == "feedback.export":
                data = feedback.export(project, args.slug, _read_input(args.input), args.output)
                return envelope(args.operation, "ok", project=project, data=data)
            value = _read_input(args.input)
            bundle_revision = feedback.validate_bundle(value)
            return envelope(args.operation, "ok", project=project, data={"valid": True, "revision": bundle_revision})
        return _dispatch_agent(args, project)
    if args.command == "docs":
        if args.operation == "docs.map":
            entries, source = isotope_docs.map_entries(project)
            return envelope(args.operation, "ok", project=project, source=source, data={"entries": entries})
        if args.operation == "docs.section":
            data, source = isotope_docs.section(project, args.path, args.section_id)
            return envelope(args.operation, "ok", project=project, source=source, data=data)
        if args.operation == "docs.validate":
            data, source = isotope_docs.validate_docs(project)
            return envelope(args.operation, "ok", project=project, source=source, data=data)
    raise IsotopeError("usage", "A command is required.", EXIT_USAGE)


def _requested_format(raw: list[str]) -> str:
    """Best-effort format sniff so even usage errors honor the caller's choice."""
    for index, token in enumerate(raw):
        if token == "--format=text" or (token == "--format" and raw[index + 1:index + 2] == ["text"]):
            return "text"
    return "json"


def _rewrite_compound_verb(command: list[str]) -> list[str]:
    """`specimen change revise` and `specimen outcome set` are single grammar nodes."""
    if command[:1] == ["specimen"] and command[1:3] in (["change", "revise"], ["outcome", "set"]):
        return ["specimen", "-".join(command[1:3])] + command[3:]
    return command


def _normalize_transport_options(argv: list[str]) -> list[str]:
    """Allow root transport options at any command depth."""
    transport: list[str] = []
    command: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in ("--format", "--project"):
            transport.append(token)
            if index + 1 < len(argv):
                transport.append(argv[index + 1])
                index += 2
                continue
        elif token.startswith("--format=") or token.startswith("--project="):
            transport.append(token)
            index += 1
            continue
        command.append(token)
        index += 1
    return transport + _rewrite_compound_verb(command)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw = _normalize_transport_options(list(argv) if argv is not None else sys.argv[1:])
    output_format = _requested_format(raw)
    operation = "cli"
    project = None
    try:
        args = parser.parse_args(raw)
        output_format = args.format
        operation = getattr(args, "operation", operation)
        if args.command == "schema":
            operation = "schema.show" if args.entity else "schema.list"
        project = resolve_project(args.project)
        payload = _dispatch(args, project)
        code = 0
    except IsotopeError as exc:
        payload = error_envelope(operation, exc, project)
        code = exc.exit_code
    except Exception as exc:  # pragma: no cover - last-resort contract protection
        internal = IsotopeError("internal", "An unexpected internal error occurred.", EXIT_INTERNAL, {"type": type(exc).__name__})
        payload = error_envelope(operation, internal, project)
        code = internal.exit_code
    print(render(payload, output_format))
    return code
