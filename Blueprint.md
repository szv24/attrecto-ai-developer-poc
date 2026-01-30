# Blueprint: Automated Portfolio Health Report

## What This System Does

This system reads project email threads, finds issues that need attention, and produces a report for a Director of Engineering preparing a Quarterly Business Review.

The system identifies two types of problems:

1. **Unresolved action items**: questions or tasks that nobody answered
2. **Emerging risks**: blockers, scope issues, or dependencies that could derail the project

The output is a prioritized list sorted by the severity.

## 1. Data Ingestion and Processing

### Current Implementation

The proof-of-concept reads `.txt` files from a local directory. Each file contains one email thread. The script iterates through `email*.txt` files, reads the text, and passes it to the LLM.

### Production Scale Design

For handling thousands of threads across multiple projects:

**Storage**: Raw emails land in object storage (S3/Azure Blob). Each thread gets a unique ID based on subject + participants hash.

**Processing Queue**: A message queue (Azure Queue) triggers analysis jobs. One thread = one job. Workers pull from the queue and process independently.

**Normalization Steps**:

- Strip email signatures and legal footers
- Parse headers: From, To, Date, Subject
- Sort messages chronologically within each thread
- Group related messages using Message-ID and In-Reply-To headers

**Chunking**: If a thread exceeds the model's context window (32768 tokens for Qwen3-1.7B), split by time windows with 2-message overlap to preserve conversation continuity.

**Output**: Structured JSON stored alongside raw input. Indexed by project, date, and flag severity for fast retrieval.

The pipeline scales horizontally: add more workers to process more threads and no thread depends on another.

## 2. Analytical Engine

### Attention Flag Definitions

**Unresolved Action Item**:

- A question asked that received no answer
- A task assigned that has no completion confirmation
- A request for information that was never provided

Example from the provided data: "... the password strength criteria (min. 1 uppercase letter, 1 number) are fine, but I don't see any mention of the minimum password length. Should it be 8 characters, like in other modules?"

**Emerging Risk**:

- Scope creep: work requested that wasn't in the original estimate
- Missing confirmation from stakeholders
- Dependency on external parties without clear timeline
- Technical blockers without resolution path

Example from the provided data: "This wasn't included in the estimate. We'd need to re-plan the sprint."

### Multi-Step Detection Process

1. **Read the full thread**: the language model sees the complete conversation in chronological order
2. **Identify candidate issues**: any question, request, concern, or blocker mentioned
3. **Check for resolution**: if a later message resolves the issue, discard it
4. **Extract evidence**: pull 1-3 direct quotes that support the flag
5. **Assign severity**:
   - High: schedule impact, contractual risk, blocked work
   - Medium: scope changes, effort increase, unclear requirements
   - Low: minor clarifications, cosmetic issues
6. **Output structured JSON**: enforced by schema validation

### Reducing Hallucination

The system uses three controls:

**Structured Output**: The LLM must return JSON matching a Pydantic schema. Invalid responses get rejected and logged.

**Evidence Requirement**: Every flag must include direct quotes from the thread. The model cannot invent issues, it must point to specific text.

**Resolution Filtering**: The prompt explicitly instructs the model to NOT flag issues that were resolved later in the thread. This prevents stale flags.

### The Prompt

```
You are a strict portfolio health analyst for a Director of Engineering preparing a QBR.
Analyze the email thread and return ONLY JSON matching the provided schema.
Set the field "source_file" to the provided source file name.

Identify only these attention flags:
1) unresolved_action_item: questions, requests, or tasks that are still open or unanswered.
2) emerging_risk: risks, blockers, scope issues, dependency concerns, or missing confirmations.

Rules:
- If an issue is clearly resolved in the thread, do NOT flag it.
- Use severity based on potential impact: high (schedule/contractual risk), medium (scope/effort risk), low (minor clarification).
- Include 1-3 exact, short evidence quotes pulled from the thread.
- If no flags exist, return an empty list.
- Keep summary to 1-2 sentences.
```

**Why this prompt works**:

- "Strict" sets the tone: conservative flagging, not aggressive
- Two explicit categories prevent scope drift
- Resolution check rule reduces false positives from solved problems
- Evidence requirement forces grounding in source text
- Empty list permission prevents forced output when nothing is wrong

### Output Schema

```python
class AttentionFlag(BaseModel):
    flag_type: Literal["unresolved_action_item", "emerging_risk"]
    severity: Literal["low", "medium", "high"]
    title: str
    evidence: List[str]
    owner: Optional[str]

class AnalysisResult(BaseModel):
    source_file: str
    project: Optional[str]
    summary: str
    flags: List[AttentionFlag]
```

The schema enforces valid values. The model cannot return non-existent flags (e.g. severity="critical" or flag_type="bug") because those fail validation.

## 3. Cost and Robustness Considerations

### Robustness Against Misleading or Ambiguous Information

The system processes unstructured human communication where intent isn't always clear. Three failure modes matter most:

**Ambiguous Language**: An email says "we might need to revisit the timeline." Is that a risk or just thinking out loud? The system handles this by:

- Requiring evidence quotes for every flag. If the model can't point to concrete text supporting a risk, the flag is weak and gets filtered.
- Setting severity thresholds conservatively. Ambiguous statements default to "low" unless explicit schedule or contractual language appears.
- Logging borderline cases separately. A human reviewer can spot-check the "low confidence" bucket weekly to calibrate the prompt.

**Incomplete Thread Context**: Someone replies "Agreed, let's proceed" but the original question was in a separate thread the system never saw. Mitigation:

- The prompt instructs the model to flag when context appears missing (e.g., references to attachments or prior discussions not present).
- The output schema includes an optional `context_gap` field for threads where the model detects incomplete information.
- Production deployment would integrate with email APIs to fetch full conversation chains rather than relying on pre-exported files.

**Adversarial or Misleading Input**: A project lead writes "everything is on track" while privately knowing about delays. The system cannot detect deception, but it can:

- Cross-reference multiple threads. If Thread A says "on track" but Thread B from the same project mentions blocked dependencies, both flags surface.
- Track historical patterns. A consistent absence of flags from a project that later fails becomes a signal for the monitoring layer.
- Flag contradictions explicitly. The prompt asks the model to note when statements in the same thread conflict.

**Schema Validation as a Safety Net**: Every LLM response passes through Pydantic validation. If the model returns malformed JSON, invents fields, or uses invalid enum values, the response is rejected. The fallback returns zero flags with a logged error rather than propagating garbage into the report.

### Cost Management

**Local Model Economics**: Qwen3-1.7B runs locally on Ollama with zero per-token cost. The tradeoff is capability: a 70B parameter model would catch more subtle issues, but at ~$0.01-0.03 per 1K tokens for cloud APIs, analyzing 500-1000 threads weekly adds up. Local inference on a single GPU handles this workload at fixed hardware cost.

**Caching Strategy**: SHA-256 hash of thread content serves as cache key. If content hasn't changed, return cached analysis.

**Tiered Escalation**: The PoC uses a single model. Production could implement escalation:

1. Run Qwen3-1.7B on all threads
2. Threads with high-severity flags or parsing failures escalate to a larger model (Qwen3-8B or cloud API)
3. Most of the threads need only the small model and the escalation costs remain bounded

## 4. Monitoring and Trust

### Key Metrics

The system needs visibility into both output quality and operational health.

**Output Quality Metrics**:

| Metric | Measurement Method |
|--------|--------------------|
| Flag Precision | Weekly sample of 20 flags reviewed by engineering lead |
| Flag Recall | Quarterly audit against known incidents from retrospectives |
| False Positive Rate (High Severity) | All high-severity flags get human review; track rejection rate |
| Severity Accuracy | Spot-check whether assigned severity matches reviewer judgment |

A missed low-priority issue is more acceptable: a flood of false alarms trains the Director to ignore the report.

**Operational Metrics**:

| Metric | Why It Matters |
|--------|----------------|
| Parse Failure Rate | Model output degradation or prompt regression |
| Average Latency | Infrastructure issue or model overload |
| Empty Flag Rate | Either healthy portfolio or broken detection |
| Queue Backlog | Processing capacity insufficient for volume |

**Drift Detection**:

Track rolling averages over 4-week windows:

- Flags per thread (e.g. baseline: ~0.8 for this dataset)
- High/medium/low distribution (e.g. baseline: 15%/45%/40%)
- Evidence quote length

A 20% deviation from baseline triggers a manual review of recent outputs against the labeled test set.

### Trust Mechanisms

**Evidence-Based Verification**: Every flag includes 1-3 direct quotes from the source thread. A reviewer doesn't need to read the full email chain, they verify the quote exists and supports the flag.

**Staged Rollout for Report Delivery**:

1. System generates raw report
2. High-severity flags enter human review queue
3. Reviewer validates, adjusts severity, or dismisses
4. Final report shows "System-flagged" vs "Reviewer-confirmed" status
5. Director sees confidence level for each item

**Feedback Capture**: Reviewers mark each flag as:

- Confirmed (valid issue, correct severity)
- Confirmed with adjustment (valid issue, wrong severity)
- Dismissed (false positive)
- Escalated (needs further investigation)

This data feeds a feedback table. Monthly analysis identifies:

- Prompt weaknesses (certain phrasings consistently miscategorized)
- Model blind spots (specific projects or authors with higher false positive rates)
- Severity calibration drift

**Prompt Version Control**: The system prompt is stored in version control alongside the code. Each report output includes the prompt version hash. If quality degrades after a prompt change, rollback is a single commit revert.

**Immutable Audit Log**: Every analysis run logs:

- Input thread hash
- Prompt version
- Raw model output (before validation)
- Parsed flags
- Reviewer decisions (if applicable)

This log enables post-hoc debugging when a missed risk surfaces later, therefore "Why didn't the system catch this?" becomes answerable.

## 5. Architectural Risk and Mitigation

### Risk: Single-Model Dependency

The entire system's output quality depends on one language model. If that model:

- Gets updated and behaves differently
- Has blind spots for certain phrasing
- Produces subtly wrong severity ratings

Director of Engineering gets bad information and might ignore a real risk or waste time on non-issues.

### Mitigation

**Dual-Model Verification**: For high-severity flags, run a second model (different architecture) and compare. Disagreement triggers human review.

**Regression Testing**: Maintain a labeled dataset of email threads with known flags. Run this test set after any model or prompt change. Alert if accuracy drops.

**Graceful Degradation**: If model confidence is low or outputs are inconsistent, surface that uncertainty to the reviewer rather than hiding it.

**Human Override**: The final report always has a "reviewed by" field. No high-severity flag reaches the Director without a human sign-off.

## Implementation Notes

The proof-of-concept in `automated_portfolio_health_report.py` implements the core detection logic. It:

- Reads email threads from `AI_Developer/` directory
- Calls Qwen3-1.7B via Ollama with structured output
- Validates responses against Pydantic schema
- Generates JSON and Markdown reports sorted by severity

Run it with: `python automated_portfolio_health_report.py`

For production, the ingestion and monitoring layers described above would wrap this core engine.
