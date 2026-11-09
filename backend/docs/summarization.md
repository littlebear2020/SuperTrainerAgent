# Conversation Summarization

DeerFlow includes automatic conversation summarization to handle long conversations that approach model token limits. When enabled, the system automatically condenses older messages while preserving recent context.

## Overview

The summarization feature uses LangChain's `SummarizationMiddleware` to monitor conversation history and trigger summarization based on configurable thresholds. When activated, it:

1. Monitors message token counts in real-time
2. Triggers summarization when thresholds are met
3. Keeps recent messages intact while summarizing older exchanges
4. Maintains AI/Tool message pairs together for context continuity
5. Injects the summary back into the conversation

## Configuration

Summarization is configured in `config.yaml` under the `summarization` key:

```yaml
summarization:
  enabled: true
  model_name: null  # Use default model or specify a lightweight model

  # Trigger conditions (OR logic - any condition triggers summarization)
  trigger:
    - type: tokens
      value: 4000
    # Additional triggers (optional)
    # - type: messages
    #   value: 50
    # - type: fraction
    #   value: 0.8  # 80% of model's max input tokens

  # Context retention policy
  keep:
    type: messages
    value: 20

  # Token trimming for summarization call
  trim_tokens_to_summarize: 30000

  # Custom summary prompt (null = built-in DeerFlow default, which extends the
  # LangChain default with a mandatory "## 工作状态与产物清单 (WORKING STATE)"
  # section so working state survives compression)
  summary_prompt: null

  # Anti-compression-loop reuse guard: while the trigger condition holds, the
  # previous summary is reused (no summary-model call) until the conversation
  # grew by at least this many messages OR approximate tokens since the stored
  # summarization_checkpoint. Both 0 = legacy re-summarize-every-call behavior.
  resummarize_min_new_messages: 20
  resummarize_min_new_tokens: 30000

  # Tool names treated as skill file reads for skill rescue
  skill_file_read_tool_names:
    - read_file
    - read
    - view
    - cat
```

### Configuration Options

#### `enabled`
- **Type**: Boolean
- **Default**: `false`
- **Description**: Enable or disable automatic summarization

#### `model_name`
- **Type**: String or null
- **Default**: `null` (uses default model)
- **Description**: Model to use for generating summaries. Recommended to use a lightweight, cost-effective model like `gpt-4o-mini` or equivalent.

#### `trigger`
- **Type**: Single `ContextSize` or list of `ContextSize` objects
- **Required**: Not enforced by code — but with `enabled: true` and no `trigger` configured, summarization never fires (silently ineffective)
- **Description**: Thresholds that trigger summarization. Uses OR logic - summarization runs when ANY threshold is met.

**ContextSize Types:**

1. **Token-based trigger**: Activates when token count reaches the specified value
   ```yaml
   trigger:
     type: tokens
     value: 4000
   ```

2. **Message-based trigger**: Activates when message count reaches the specified value
   ```yaml
   trigger:
     type: messages
     value: 50
   ```

3. **Fraction-based trigger**: Activates when token usage reaches a percentage of the model's maximum input tokens
   ```yaml
   trigger:
     type: fraction
     value: 0.8  # 80% of max input tokens
   ```

**Multiple Triggers:**
```yaml
trigger:
  - type: tokens
    value: 4000
  - type: messages
    value: 50
```

#### `keep`
- **Type**: `ContextSize` object
- **Default**: `{type: messages, value: 20}`
- **Description**: Specifies how much recent conversation history to preserve after summarization.

**Examples:**
```yaml
# Keep most recent 20 messages
keep:
  type: messages
  value: 20

# Keep most recent 3000 tokens
keep:
  type: tokens
  value: 3000

# Keep most recent 30% of model's max input tokens
keep:
  type: fraction
  value: 0.3
```

#### `trim_tokens_to_summarize`
- **Type**: Integer or null
- **Default**: `30000`
- **Description**: Maximum tokens to include when preparing messages for the summarization call itself. `null` does NOT skip trimming — `agent.py` only forwards the value when it is not `None`, so `null` falls back to the code default of 30000. Raised from 4000 to 30000 on 2026-09-22: a 4000-token tail window made summaries lose working state (produced files, manifests, in-progress steps).

#### `summary_prompt`
- **Type**: String or null
- **Default**: `null` (uses the built-in DeerFlow default prompt)
- **Description**: Custom prompt template for generating summaries. The prompt should guide the model to extract the most important context. The built-in default (`DEERFLOW_DEFAULT_SUMMARY_PROMPT`) extends the LangChain default (SESSION INTENT / SUMMARY / ARTIFACTS / NEXT STEPS) with a mandatory `## 工作状态与产物清单 (WORKING STATE)` section: exact produced-file paths, manifest files, the interrupted/in-progress step, and an explicit instruction that completed steps must not be redone.

#### `resummarize_min_new_messages`
- **Type**: Integer (≥ 0)
- **Default**: `20`
- **Description**: Anti-compression-loop guard. DeerFlow's summarization never mutates `state["messages"]` (only the inference window is replaced), so once a trigger condition holds it would re-summarize on every model call. With a stored `summarization_checkpoint`, the previous summary is reused until the conversation has grown by at least this many new messages OR `resummarize_min_new_tokens` approximate tokens. Setting BOTH knobs to `0` restores the legacy re-summarize-every-call behavior (rollback).

#### `resummarize_min_new_tokens`
- **Type**: Integer (≥ 0)
- **Default**: `30000`
- **Description**: Token-growth half of the reuse guard (see `resummarize_min_new_messages`). Growth past EITHER threshold triggers a fresh summary.

#### `preserve_recent_skill_count`
- **Type**: Integer (≥ 0)
- **Default**: `5`
- **Description**: Number of most-recently-loaded skill files (tool results whose tool name is in `skill_file_read_tool_names` and whose target path is under `skills.container_path`, e.g. `/mnt/skills/...`) that are rescued from summarization. Prevents the agent from losing skill instructions after compression. Set to `0` to disable skill rescue entirely.

#### `preserve_recent_skill_tokens`
- **Type**: Integer (≥ 0)
- **Default**: `25000`
- **Description**: Total token budget reserved for rescued skill reads. Once this budget is exhausted, older skill bundles are allowed to be summarized.

#### `preserve_recent_skill_tokens_per_skill`
- **Type**: Integer (≥ 0)
- **Default**: `5000`
- **Description**: Per-skill token cap. Any individual skill read whose tool result exceeds this size is not rescued (it falls through to the summarizer like ordinary content).

#### `skill_file_read_tool_names`
- **Type**: List of strings
- **Default**: `["read_file", "read", "view", "cat"]`
- **Description**: Tool names treated as skill file reads during summarization rescue. A tool call is only eligible for skill rescue when its name appears in this list and its target path is under `skills.container_path`.

#### `skill_ledger_max_entries`
- **Type**: Integer (≥ 0)
- **Default**: `8`
- **Description**: Max entries (name + path) in the compact skill ledger kept in the inference window after summarization compresses un-rescued skill reads. The ledger carries no skill bodies — it tells the agent which `SKILL.md` files it already loaded so it can re-read them. Set to `0` to disable the ledger. (Also recorded in `config.example.yaml`.)

**Default Prompt Behavior:**
The default LangChain prompt instructs the model to:
- Extract highest quality/most relevant context
- Focus on information critical to the overall goal
- Avoid repeating completed actions
- Return only the extracted context

## How It Works

### Summarization Flow

1. **Monitoring**: Before each model call, the middleware counts tokens in the message history
2. **Trigger Check**: If any configured threshold is met, summarization is triggered
3. **Message Partitioning**: Messages are split into:
   - Messages to summarize (older messages beyond the `keep` threshold)
   - Messages to preserve (recent messages within the `keep` threshold)
4. **Summary Generation**: The model generates a concise summary of the older messages
5. **Context Replacement (inference window only)**: DeerFlow's subclass never mutates `state["messages"]` or checkpoints — the full history is always preserved (regenerate/edit-rerun unaffected). Only the per-call inference window is replaced:
   - A single summary message is prepended
   - Recent messages are preserved
6. **Reuse Guard**: the fresh summary is persisted to `ThreadState.summarization_checkpoint` (summary text + message count + approx tokens + frozen cutoff index + timestamp). While a trigger condition stays true, subsequent model calls reuse the stored summary with its **frozen cutoff C0**: the window is `[summary covering messages[:C0]] + messages[C0:]` — i.e. everything since the summarization point stays visible (skill rescue still scans the current state), and the preserved tail grows, bounded by the reuse thresholds, until growth exceeds `resummarize_min_new_messages` OR `resummarize_min_new_tokens`, which triggers a fresh summary. No messages silently leave the model's view during reuse. This eliminates the pre-2026-09-22 "compression loop" where every model call re-ran the summary model once the message count crossed the trigger.
7. **AI/Tool Pair Protection**: The system ensures AI messages and their corresponding tool messages stay together
8. **Skill Rescue**: Before the summary is generated, the most recently loaded skill files (tool results whose tool name is in `skill_file_read_tool_names` and whose target path is under `skills.container_path`) are lifted out of the summarization set and prepended to the preserved tail. Selection walks newest-first under three budgets: `preserve_recent_skill_count`, `preserve_recent_skill_tokens`, and `preserve_recent_skill_tokens_per_skill`. Rescue splits at tool_call granularity: only the skill-related tool_call + ToolMessage pairs are lifted out — the same AIMessage's other tool calls stay in the summarization set (summarization_middleware.py:540-556). When skill reads exceed the rescue budgets and get folded into the summary, `_assemble_window` injects a compact name+path ledger (no bodies, capped at `skill_ledger_max_entries`, newest entries kept) between the summary block and the preserved tail, so the agent knows which `SKILL.md` to re-read.

### Token Counting

- Uses approximate token counting based on character count
- For Anthropic models: ~3.3 characters per token
- For other models: Uses LangChain's default estimation
- Can be customized with a custom `token_counter` function

### Message Preservation

The middleware intelligently preserves message context:

- **Recent Messages**: Always kept intact based on `keep` configuration
- **AI/Tool Pairs**: Never split - if a cutoff point falls within tool messages, the system adjusts to keep the entire AI + Tool message sequence together
- **Summary Format**: Summary is injected as a HumanMessage with the format:
  ```
  Here is a summary of the conversation to date:

  [Generated summary text]
  ```

## Best Practices

### Choosing Trigger Thresholds

1. **Token-based triggers**: Recommended for most use cases
   - Set to 60-80% of your model's context window
   - Example: For 8K context, use 4000-6000 tokens

2. **Message-based triggers**: Useful for controlling conversation length
   - Good for applications with many short messages
   - Example: 50-100 messages depending on average message length

3. **Fraction-based triggers**: Ideal when using multiple models
   - Automatically adapts to each model's capacity
   - Example: 0.8 (80% of model's max input tokens)

### Choosing Retention Policy (`keep`)

1. **Message-based retention**: Best for most scenarios
   - Preserves natural conversation flow
   - Recommended: 15-25 messages

2. **Token-based retention**: Use when precise control is needed
   - Good for managing exact token budgets
   - Recommended: 2000-4000 tokens

3. **Fraction-based retention**: For multi-model setups
   - Automatically scales with model capacity
   - Recommended: 0.2-0.4 (20-40% of max input)

### Model Selection

- **Recommended**: Use a lightweight, cost-effective model for summaries
  - Examples: `gpt-4o-mini`, `claude-haiku`, or equivalent
  - Summaries don't require the most powerful models
  - Significant cost savings on high-volume applications

- **Default**: If `model_name` is `null`, uses the default model
  - May be more expensive but ensures consistency
  - Good for simple setups

### Optimization Tips

1. **Balance triggers**: Combine token and message triggers for robust handling
   ```yaml
   trigger:
     - type: tokens
       value: 4000
     - type: messages
       value: 50
   ```

2. **Conservative retention**: Keep more messages initially, adjust based on performance
   ```yaml
   keep:
     type: messages
     value: 25  # Start higher, reduce if needed
   ```

3. **Trim strategically**: Limit tokens sent to summarization model
   ```yaml
   trim_tokens_to_summarize: 4000  # Prevents expensive summarization calls
   ```

4. **Monitor and iterate**: Track summary quality and adjust configuration

## Troubleshooting

### Summary Quality Issues

**Problem**: Summaries losing important context

**Solutions**:
1. Increase `keep` value to preserve more messages
2. Decrease trigger thresholds to summarize earlier
3. Customize `summary_prompt` to emphasize key information
4. Use a more capable model for summarization

### Performance Issues

**Problem**: Summarization calls taking too long

**Solutions**:
1. Use a faster model for summaries (e.g., `gpt-4o-mini`)
2. Reduce `trim_tokens_to_summarize` to send less context
3. Increase trigger thresholds to summarize less frequently

### Token Limit Errors

**Problem**: Still hitting token limits despite summarization

**Solutions**:
1. Lower trigger thresholds to summarize earlier
2. Reduce `keep` value to preserve fewer messages
3. Check if individual messages are very large
4. Consider using fraction-based triggers

## Implementation Details

### Code Structure

- **Configuration**: `packages/harness/deerflow/config/summarization_config.py`
- **Integration**: `packages/harness/deerflow/agents/lead_agent/agent.py`
- **Middleware**: `DeerFlowSummarizationMiddleware` (`packages/harness/deerflow/agents/middlewares/summarization_middleware.py`), a subclass of LangChain's `langchain.agents.middleware.SummarizationMiddleware`

### Middleware Order

Summarization runs after ThreadData and Sandbox initialization but before Title and Clarification. The list below shows **relative positions only** (~10 middlewares omitted in between); the authoritative full order is the 「中间件链」 section of `backend/AGENTS.md`:

1. ThreadDataMiddleware
2. SandboxMiddleware
3. **SummarizationMiddleware** ← Runs here
4. TitleMiddleware
5. ClarificationMiddleware

### State Management

- The conversation history in `state["messages"]` and checkpoints is NEVER rewritten — full history is always preserved; only the per-call inference window is replaced with `[summary, preserved tail]`
- The one piece of new state is `ThreadState.summarization_checkpoint` (summary text, source message count, approx tokens, frozen cutoff index, timestamp), written by the middleware's `after_model` when a fresh summary is produced, and consumed by the reuse guard on later calls; the frozen cutoff keeps every message since the summarization point in the reused window (tail grows bounded by the reuse thresholds)
- Configuration is loaded per agent creation (config mtime hot-reload applies)

## Example Configurations

### Minimal Configuration
```yaml
summarization:
  enabled: true
  trigger:
    type: tokens
    value: 4000
  keep:
    type: messages
    value: 20
```

### Production Configuration
```yaml
summarization:
  enabled: true
  model_name: gpt-4o-mini  # Lightweight model for cost efficiency
  trigger:
    - type: tokens
      value: 6000
    - type: messages
      value: 75
  keep:
    type: messages
    value: 25
  trim_tokens_to_summarize: 5000
```

### Multi-Model Configuration
```yaml
summarization:
  enabled: true
  model_name: gpt-4o-mini
  trigger:
    type: fraction
    value: 0.7  # 70% of model's max input
  keep:
    type: fraction
    value: 0.3  # Keep 30% of max input
  trim_tokens_to_summarize: 4000
```

### Conservative Configuration (High Quality)
```yaml
summarization:
  enabled: true
  model_name: gpt-4  # Use full model for high-quality summaries
  trigger:
    type: tokens
    value: 8000
  keep:
    type: messages
    value: 40  # Keep more context
  trim_tokens_to_summarize: null  # null = fall back to code default 30000 (NOT "no trimming")
```

## References

- [LangChain Summarization Middleware Documentation](https://docs.langchain.com/oss/python/langchain/middleware/built-in#summarization)
- [LangChain Source Code](https://github.com/langchain-ai/langchain)
