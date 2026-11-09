"""Tests for translating a subagent turn budget into a LangGraph recursion limit.

Covers:
- Per-turn and per-invocation node counting from a middleware chain
- Sync-only, async-only, and both-sided hooks each costing exactly one node
- ``resolve_recursion_limit`` arithmetic, including a non-positive budget
- A pin test that compiles real ``create_agent`` graphs and binary-searches the
  smallest ``recursion_limit`` that completes N turns, so the formula is checked
  against the installed LangChain rather than against its documentation
- Jump detection across every hook LangChain wires a jump edge for, and the
  shortfall each kind of jump causes against a compiled graph
- P2-2 (2026-09-29): the bounded retry margin priced in for after_model->model
  jumps — unit arithmetic, the assembled subagent chain (413/417), and pinned
  against a real graph both within and beyond the margin
"""

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError

from deerflow.subagents.turn_budget import count_invocation_steps, count_jump_margin_steps, count_turn_steps, find_jumping_hooks, resolve_recursion_limit


def _middleware(name: str, *hooks: str) -> AgentMiddleware:
    """Build a no-op middleware overriding exactly *hooks*."""
    namespace = {hook: (lambda self, state, runtime: None) for hook in hooks}
    return type(name, (AgentMiddleware,), namespace)()


class TestNodeCounting:
    def test_bare_chain_costs_model_plus_tools(self):
        assert count_turn_steps([]) == 2
        assert count_invocation_steps([]) == 0

    def test_each_model_hook_adds_one_node(self):
        chain = [_middleware("Before", "before_model"), _middleware("After", "after_model")]

        assert count_turn_steps(chain) == 4

    def test_one_middleware_implementing_both_model_hooks_adds_two_nodes(self):
        chain = [_middleware("Both", "before_model", "after_model")]

        assert count_turn_steps(chain) == 4

    def test_async_only_hook_costs_the_same_as_sync(self):
        """LangChain compiles one node per sync/async pair, whichever side exists."""
        sync_only = [_middleware("Sync", "after_model")]
        async_only = [_middleware("Async", "aafter_model")]
        both_sides = [_middleware("Both", "after_model", "aafter_model")]

        assert count_turn_steps(sync_only) == count_turn_steps(async_only) == count_turn_steps(both_sides) == 3

    def test_agent_hooks_are_charged_once_per_invocation_not_per_turn(self):
        chain = [_middleware("Lifecycle", "before_agent", "after_agent")]

        assert count_turn_steps(chain) == 2
        assert count_invocation_steps(chain) == 2

    def test_middleware_without_lifecycle_hooks_is_free(self):
        """A wrap-only middleware runs inside the model node, so it adds none."""

        class WrapOnly(AgentMiddleware):
            def wrap_model_call(self, request, handler):
                return handler(request)

        assert count_turn_steps([WrapOnly()]) == 2
        assert count_invocation_steps([WrapOnly()]) == 0

    def test_object_without_hooks_contributes_nothing(self):
        """A duck-typed entry compiles to no node, so a missing hook is not an override."""
        assert count_turn_steps([object()]) == 2
        assert count_invocation_steps([object()]) == 0


class TestResolveRecursionLimit:
    def test_scales_turns_by_the_assembled_chain(self):
        """Regression: passing ``max_turns`` through divided the budget by chain depth."""
        chain = [
            _middleware("Before", "before_model"),
            _middleware("After1", "after_model"),
            _middleware("After2", "after_model"),
            _middleware("After3", "after_model"),
            _middleware("After4", "after_model"),
        ]

        assert resolve_recursion_limit(150, chain) == 150 * 7

    def test_adds_invocation_steps_on_top_of_the_turn_budget(self):
        chain = [_middleware("Loop", "before_model"), _middleware("Lifecycle", "before_agent", "after_agent")]

        assert resolve_recursion_limit(10, chain) == 10 * 3 + 2

    def test_non_positive_budget_still_buys_one_turn(self):
        """LangGraph rejects a limit below 1; a misconfigured budget must not fail the run."""
        assert resolve_recursion_limit(0, []) == 2
        assert resolve_recursion_limit(-5, []) == 2


class TestFindJumpingHooks:
    """A jump re-enters the loop without traversing ``tools``.

    The flat per-turn multiplier does not model that. ``after_model``-family
    jumps are bounded (max_retries) and now carry a priced-in margin (P2-2,
    2026-09-29); every other shape stays data-dependent, so the condition is
    reported rather than priced in.
    """

    def test_reports_the_middleware_and_hook_that_declares_a_jump(self):
        class Jumper(AgentMiddleware):
            @hook_config(can_jump_to=["model"])
            def after_model(self, state, runtime):
                return None

        assert find_jumping_hooks([Jumper()]) == [("Jumper", "after_model")]

    def test_reports_an_async_hook_declaration(self):
        class AsyncJumper(AgentMiddleware):
            @hook_config(can_jump_to=["end"])
            async def abefore_model(self, state, runtime):
                return None

        assert find_jumping_hooks([AsyncJumper()]) == [("AsyncJumper", "abefore_model")]

    def test_plain_hooks_declare_no_jump(self):
        chain = [_middleware("Before", "before_model"), _middleware("After", "after_model")]

        assert find_jumping_hooks(chain) == []

    @pytest.mark.parametrize("hook", ["before_agent", "abefore_agent", "after_agent", "aafter_agent"])
    def test_agent_level_hooks_are_reported(self, hook):
        """They run once per invocation, but a jump out of them is not free.

        ``after_agent`` can re-enter the loop after it finished, and a
        ``before_agent`` jump to ``tools`` runs a step no turn paid for.
        """

        async def async_hook(self, state, runtime):
            return None

        def sync_hook(self, state, runtime):
            return None

        implementation = async_hook if hook in {"abefore_agent", "aafter_agent"} else sync_hook
        LifecycleJumper = type("LifecycleJumper", (AgentMiddleware,), {hook: hook_config(can_jump_to=["model"])(implementation)})

        assert find_jumping_hooks([LifecycleJumper()]) == [("LifecycleJumper", hook)]

    def test_object_without_hooks_is_not_reported(self):
        assert find_jumping_hooks([object()]) == []


class _ScriptedModel(BaseChatModel):
    """Emits ``turns - 1`` tool calls, then a final text answer."""

    turns: int
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        if self.calls < self.turns:
            message = AIMessage(content="", tool_calls=[{"name": "ping", "args": {}, "id": f"call-{self.calls}"}])
        else:
            message = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=message)])


@tool
def ping() -> str:
    """Return a fixed token."""
    return "pong"


def _smallest_limit_completing(chain: list[AgentMiddleware], turns: int) -> int:
    """Binary-search the smallest ``recursion_limit`` that completes *turns* turns."""
    low, high = 1, 512
    while low < high:
        candidate = (low + high) // 2
        agent = create_agent(model=_ScriptedModel(turns=turns), tools=[ping], middleware=chain, checkpointer=False)
        try:
            agent.invoke({"messages": [("user", "go")]}, {"recursion_limit": candidate})
        except GraphRecursionError:
            low = candidate + 1
        else:
            high = candidate
    return low


class TestFormulaMatchesCompiledGraph:
    """Pins the arithmetic against the installed LangChain, not against its docs.

    If LangChain changes how it compiles middleware hooks into nodes, the
    subagent turn budget silently drifts again. These cases fail instead.
    """

    @pytest.mark.parametrize(
        "hooks",
        [
            (),
            (("Before", "before_model"),),
            (("After", "after_model"),),
            (("Before", "before_model"), ("After1", "after_model"), ("After2", "after_model")),
            (("Lifecycle", "before_agent", "after_agent"), ("After", "after_model")),
        ],
    )
    @pytest.mark.parametrize("turns", [1, 3])
    def test_resolved_limit_is_exactly_what_the_graph_needs(self, hooks, turns):
        chain = [_middleware(*spec) for spec in hooks]

        assert resolve_recursion_limit(turns, chain) == _smallest_limit_completing(chain, turns)

    @pytest.mark.parametrize(
        ("hook", "destination", "count_model_calls", "jump_cost"),
        [
            # after_model->model moved to its own tests below: P2-2 (2026-09-29)
            # prices a bounded retry margin into resolve_recursion_limit, so a
            # single such jump no longer makes the limit a lower bound.
            # Re-enters the loop after it finished: a model pass, then the
            # after_agent node again on the way out.
            pytest.param("after_agent", "model", False, 2, id="after_agent-to-model"),
            # ``end`` routes back to the head of the after_agent chain, so even
            # the exit is not free — why detection ignores destinations.
            pytest.param("after_agent", "end", False, 1, id="after_agent-to-end"),
            # Once per invocation, but the staged tool call runs a ``tools``
            # step outside any model turn.
            pytest.param("before_agent", "tools", True, 1, id="before_agent-to-tools"),
        ],
    )
    def test_a_jumping_hook_makes_the_limit_a_lower_bound(self, hook, destination, count_model_calls, jump_cost):
        """Why ``find_jumping_hooks`` exists, pinned against a real graph.

        The loop cases count progress off the ToolMessages actually in state, so
        a jump that re-enters the model buys no progress and is pure overhead,
        the retry/repair shape. The ``before_agent`` case stages a tool call the
        model never made, so there the model counts its own calls instead —
        otherwise the staged tool result would pass for one of its turns.
        """

        class _ToolProgressModel(_ScriptedModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
                executed = sum(1 for message in messages if getattr(message, "type", None) == "tool")
                if executed < self.turns:
                    message = AIMessage(content="", tool_calls=[{"name": "ping", "args": {}, "id": f"call-{executed}"}])
                else:
                    message = AIMessage(content="done")
                return ChatResult(generations=[ChatGeneration(message=message)])

        def jump_once(self, state, runtime):
            if self.jumped:
                return None
            self.jumped = True
            update = {"jump_to": destination}
            if destination == "tools":
                update["messages"] = [AIMessage(content="", tool_calls=[{"name": "ping", "args": {}, "id": "staged"}])]
            return update

        _JumpOnce = type(
            "_JumpOnce",
            (AgentMiddleware,),
            {"jumped": False, hook: hook_config(can_jump_to=[destination])(jump_once)},
        )

        tool_turns = 3
        budget_turns = tool_turns + 1  # the tool turns plus the turn that answers

        def run(limit):
            model = _ScriptedModel(turns=budget_turns) if count_model_calls else _ToolProgressModel(turns=tool_turns)
            create_agent(model=model, tools=[ping], middleware=[_JumpOnce()], checkpointer=False).invoke({"messages": [("user", "go")]}, {"recursion_limit": limit})

        resolved = resolve_recursion_limit(budget_turns, [_JumpOnce()])
        with pytest.raises(GraphRecursionError):
            run(resolved)
        run(resolved + jump_cost)

        assert find_jumping_hooks([_JumpOnce()]) == [("_JumpOnce", hook)]

    def test_after_model_jump_within_the_margin_is_covered(self):
        """P2-2: a single after_model->model retry is priced in, not a shortfall.

        One retry costs two super-steps and the default margin budgets
        max_retries=3 -> six, so the resolved limit completes the run on its
        own — pinned against a real compiled graph.
        """

        class _ToolProgressModel(_ScriptedModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
                executed = sum(1 for message in messages if getattr(message, "type", None) == "tool")
                if executed < self.turns:
                    message = AIMessage(content="", tool_calls=[{"name": "ping", "args": {}, "id": f"call-{executed}"}])
                else:
                    message = AIMessage(content="done")
                return ChatResult(generations=[ChatGeneration(message=message)])

        def jump_once(self, state, runtime):
            if self.jumped:
                return None
            self.jumped = True
            return {"jump_to": "model"}

        _JumpOnce = type(
            "_JumpOnce",
            (AgentMiddleware,),
            {"jumped": False, "after_model": hook_config(can_jump_to=["model"])(jump_once)},
        )

        tool_turns = 3
        budget_turns = tool_turns + 1

        resolved = resolve_recursion_limit(budget_turns, [_JumpOnce()])
        # No GraphRecursionError: the margin (6) covers the one jump (2).
        create_agent(
            model=_ToolProgressModel(turns=tool_turns),
            tools=[ping],
            middleware=[_JumpOnce()],
            checkpointer=False,
        ).invoke({"messages": [("user", "go")]}, {"recursion_limit": resolved})

    def test_after_model_jumps_beyond_the_margin_still_cap(self):
        """P2-2: the margin is bounded — beyond it the limit is a lower bound again.

        Four jumps cost eight super-steps against a margin of six, so the run
        caps two steps short at the resolved limit and completes only with the
        deficit added back.
        """

        class _ToolProgressModel(_ScriptedModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
                executed = sum(1 for message in messages if getattr(message, "type", None) == "tool")
                if executed < self.turns:
                    message = AIMessage(content="", tool_calls=[{"name": "ping", "args": {}, "id": f"call-{executed}"}])
                else:
                    message = AIMessage(content="done")
                return ChatResult(generations=[ChatGeneration(message=message)])

        jump_budget = 4

        def jump_until_exhausted(self, state, runtime):
            if self.jumped >= jump_budget:
                return None
            self.jumped += 1
            return {"jump_to": "model"}

        _JumpUpTo = type(
            "_JumpUpTo",
            (AgentMiddleware,),
            {"jumped": 0, "after_model": hook_config(can_jump_to=["model"])(jump_until_exhausted)},
        )

        tool_turns = 3
        budget_turns = tool_turns + 1

        def run(limit):
            create_agent(
                model=_ToolProgressModel(turns=tool_turns),
                tools=[ping],
                middleware=[_JumpUpTo()],
                checkpointer=False,
            ).invoke({"messages": [("user", "go")]}, {"recursion_limit": limit})

        resolved = resolve_recursion_limit(budget_turns, [_JumpUpTo()])
        # 4 jumps x 2 steps = 8 spent, margin covered 6 -> two short.
        with pytest.raises(GraphRecursionError):
            run(resolved)
        run(resolved + 2)


class TestJumpRetryMargin:
    """P2-2 (2026-09-29): bounded after_model->model jumps carry a priced margin."""

    def test_no_jump_chain_has_no_margin(self):
        chain = [_middleware("Before", "before_model"), _middleware("After", "after_model")]
        assert count_jump_margin_steps(chain) == 0
        # And resolve is unchanged for jump-free chains.
        assert resolve_recursion_limit(10, chain) == 10 * count_turn_steps(chain) + count_invocation_steps(chain)

    def test_after_model_jump_defaults_to_three_retries(self):
        class Jumper(AgentMiddleware):
            @hook_config(can_jump_to=["model"])
            def after_model(self, state, runtime):
                return None

        # No max_retries attribute on the instance -> module default 3 x 2 steps.
        assert count_jump_margin_steps([Jumper()]) == 6

    def test_instance_max_retries_is_priced(self):
        class Jumper(AgentMiddleware):
            @hook_config(can_jump_to=["model"])
            def after_model(self, state, runtime):
                return None

        jumper = Jumper()
        jumper.max_retries = 5
        assert count_jump_margin_steps([jumper]) == 10

    def test_both_sides_declaring_counts_once(self):
        class BothSides(AgentMiddleware):
            @hook_config(can_jump_to=["model"])
            def after_model(self, state, runtime):
                return None

            @hook_config(can_jump_to=["model"])
            async def aafter_model(self, state, runtime):
                return None

        # The sync/async pair compiles to a single node; don't double-count.
        assert count_jump_margin_steps([BothSides()]) == 6

    def test_uncovered_jump_shapes_get_no_margin(self):
        class AgentJumper(AgentMiddleware):
            @hook_config(can_jump_to=["model"])
            def after_agent(self, state, runtime):
                return None

        assert count_jump_margin_steps([AgentJumper()]) == 0

    def test_invalid_max_retries_falls_back_to_default(self):
        class Jumper(AgentMiddleware):
            @hook_config(can_jump_to=["model"])
            def after_model(self, state, runtime):
                return None

        jumper = Jumper()
        jumper.max_retries = "many"
        assert count_jump_margin_steps([jumper]) == 6


class TestRealSubagentChainMargin:
    """The assembled subagent chain: 407 flat + 6 retry margin = 413 (P2-2)."""

    @pytest.fixture
    def chain(self):
        from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares
        from deerflow.config.app_config import AppConfig
        from deerflow.config.guardrails_config import GuardrailsConfig
        from deerflow.config.sandbox_config import SandboxConfig

        app_config = AppConfig(sandbox=SandboxConfig(use="test"), guardrails=GuardrailsConfig(enabled=False))
        return build_subagent_runtime_middlewares(app_config=app_config, agent_name="general-purpose", lazy_init=True)

    def test_subagent_chain_budgets_the_retry_margin(self, chain):
        # Per turn: TerminalResponse's after_model pair + model + tools = 4.
        # Invocation: ThreadData 1 + Sandbox 2 + CircuitBreaker 1 + Memory 1 +
        # TerminalResponse 2 + LoopDetection 2（2026-10-14 加固批：警告瞬态化
        # 移植 #2752/#5344 带来 before_agent/after_agent 生命周期节点）= 9.
        # Margin: TerminalResponse max_retries=3 x 2 = 6.
        assert count_turn_steps(chain) == 4
        assert count_invocation_steps(chain) == 9
        assert count_jump_margin_steps(chain) == 6
        assert resolve_recursion_limit(100, chain) == 415

    def test_custom_max_retries_widens_the_margin(self, chain):
        from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware

        tuned = [TerminalResponseMiddleware(max_retries=5) if type(m).__name__ == "TerminalResponseMiddleware" else m for m in chain]
        assert count_jump_margin_steps(tuned) == 10
        assert resolve_recursion_limit(100, tuned) == 419
